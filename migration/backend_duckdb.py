#!/usr/bin/env python3
"""
Quantiv FastAPI Backend - DuckDB Migration Version
Expected Move Forecasting API with DuckDB + Parquet backend
"""

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, AsyncGenerator
import duckdb
import redis.asyncio as redis
import httpx
import structlog
from datetime import datetime, date, timedelta
import os
from contextlib import asynccontextmanager
import json
from pathlib import Path
from dotenv import load_dotenv
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

# Configure structured logging
logger = structlog.get_logger()

# Load environment variables
try:
    repo_root = Path(__file__).resolve().parents[2]
except IndexError:
    repo_root = Path(__file__).resolve().parent
env_file = ".env.production" if os.getenv("NODE_ENV") == "production" or os.getenv("ENVIRONMENT") == "production" else ".env.local"
env_path = repo_root / "config" / env_file
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# DuckDB connection manager
class DuckDBManager:
    def __init__(self, db_path: str, data_dir: str):
        self.db_path = db_path
        self.data_dir = Path(data_dir)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="duckdb")
        self._local = threading.local()
        
    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get thread-local DuckDB connection."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = duckdb.connect(self.db_path, read_only=False)
            # Configure connection for performance
            self._local.conn.execute("SET memory_limit = '2GB'")
            self._local.conn.execute("SET threads = 4")
        return self._local.conn
    
    async def execute_query(self, query: str, params: tuple = ()) -> List[Dict]:
        """Execute query asynchronously using thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor, 
            self._execute_sync, 
            query, 
            params
        )
    
    async def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict]:
        """Fetch single row asynchronously."""
        results = await self.execute_query(query, params)
        return results[0] if results else None
        
    def _execute_sync(self, query: str, params: tuple = ()) -> List[Dict]:
        """Execute query synchronously in thread."""
        conn = self.get_connection()
        try:
            if params:
                cursor = conn.execute(query, params)
            else:
                cursor = conn.execute(query)
            
            # Get column names
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            
            # Convert to list of dictionaries
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error("DuckDB query error", query=query[:100], error=str(e))
            raise
    
    def close(self):
        """Close all connections and executor."""
        self.executor.shutdown(wait=True)

# Pydantic models (same as original)
class ExpectedMoveRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol (e.g., AAPL)")
    horizons: List[str] = Field(default=["to_exp", "1d", "5d"], description="Forecast horizons")
    include_live: bool = Field(default=True, description="Include live market data")

class ExpectedMoveResponse(BaseModel):
    symbol: str
    timestamp: datetime
    forecasts: List[Dict[str, Any]]
    live_data: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any]

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    services: Dict[str, str]

class EmForecastLatestResponse(BaseModel):
    symbol: str
    exp: date
    quote_ts: datetime
    horizon: str
    em_baseline: Optional[float] = None
    band68_low: Optional[float] = None
    band68_high: Optional[float] = None
    band95_low: Optional[float] = None
    band95_high: Optional[float] = None
    metadata: Dict[str, Any]

class EmHistoryItem(BaseModel):
    quote_ts: datetime
    em_baseline: Optional[float] = None
    band68_low: Optional[float] = None
    band68_high: Optional[float] = None
    band95_low: Optional[float] = None
    band95_high: Optional[float] = None

class EmHistoryResponse(BaseModel):
    symbol: str
    exp: date
    window: str
    items: List[EmHistoryItem]
    metadata: Dict[str, Any]

class EmExpiriesResponse(BaseModel):
    symbol: str
    expiries: List[date]
    metadata: Dict[str, Any]

# Global services
duckdb_manager: DuckDBManager = None
redis_client: redis.Redis = None
http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global duckdb_manager, redis_client, http_client
    
    logger.info("🚀 Starting Quantiv API (DuckDB)...")
    
    # Initialize DuckDB
    db_path = os.getenv("DUCKDB_PATH", "./quantiv.duckdb")
    data_dir = os.getenv("DATA_DIR", "./data")
    duckdb_manager = DuckDBManager(db_path, data_dir)
    
    # Test DuckDB connection
    try:
        await duckdb_manager.execute_query("SELECT 1 as test")
        logger.info("✅ DuckDB connection established")
    except Exception as e:
        logger.error("❌ DuckDB connection failed", error=str(e))
        raise
    
    # Initialize Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    # Initialize HTTP client for Polygon
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"Authorization": f"Bearer {os.getenv('POLYGON_API_KEY', '')}"}
    )
    
    logger.info("✅ All services initialized")
    
    yield
    
    # Cleanup
    logger.info("🔄 Shutting down services...")
    duckdb_manager.close()
    await redis_client.aclose()
    await http_client.aclose()

# Create FastAPI app
app = FastAPI(
    title="Quantiv Expected Move API (DuckDB)",
    description="ML-powered options expected move forecasting with DuckDB backend",
    version="2.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://quantiv.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ExpectedMoveService:
    """Service for expected move calculations and caching"""
    
    @staticmethod
    async def get_cached_forecast(symbol: str, horizons: List[str]) -> Optional[Dict]:
        """Get cached forecast from Redis"""
        cache_key = f"em_forecast:{symbol}:{':'.join(sorted(horizons))}"
        
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                # Check if cache is still fresh (< 5 minutes)
                cached_time = datetime.fromisoformat(data['timestamp'])
                if datetime.now() - cached_time < timedelta(minutes=5):
                    return data
        except Exception as e:
            logger.warning("Cache read failed", error=str(e))
        
        return None
    
    @staticmethod
    async def cache_forecast(symbol: str, horizons: List[str], data: Dict):
        """Cache forecast in Redis"""
        cache_key = f"em_forecast:{symbol}:{':'.join(sorted(horizons))}"
        
        try:
            await redis_client.setex(
                cache_key, 
                300,  # 5 minutes TTL
                json.dumps(data, default=str)
            )
        except Exception as e:
            logger.warning("Cache write failed", error=str(e))
    
    @staticmethod
    async def get_latest_forecasts(symbol: str, horizons: List[str]) -> List[Dict]:
        """Get latest ML forecasts from DuckDB"""
        
        # Convert horizons list to SQL array format for DuckDB
        horizons_sql = "(" + ",".join([f"'{h}'" for h in horizons]) + ")"
        
        query = f"""
        SELECT 
            underlying,
            quote_ts,
            exp_date,
            horizon,
            em_baseline,
            band68_low,
            band68_high,
            band95_low,
            band95_high
        FROM em_forecasts
        WHERE underlying = ?
          AND horizon IN {horizons_sql}
          AND quote_ts >= current_timestamp - INTERVAL 1 DAY
        ORDER BY quote_ts DESC, exp_date ASC
        LIMIT 50
        """
        
        rows = await duckdb_manager.execute_query(query, (symbol,))
        return rows

    @staticmethod
    async def get_latest_for_symbol_exp(symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        """Get the latest forecast for a symbol and exp_date (MVP horizon 'to_exp')."""
        query = """
        SELECT underlying, quote_ts, exp_date, horizon,
               em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = ? AND exp_date = ? AND horizon = 'to_exp'
        ORDER BY quote_ts DESC
        LIMIT 1
        """
        
        return await duckdb_manager.fetchone(query, (symbol, exp_date))

    @staticmethod
    async def get_history_for_symbol_exp(symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        """Get timeseries for a symbol/exp_date over a window (days)."""
        query = f"""
        SELECT quote_ts, em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = ? AND exp_date = ? AND horizon = 'to_exp'
          AND quote_ts >= current_timestamp - INTERVAL {window_days} DAY
        ORDER BY quote_ts ASC
        """
        
        return await duckdb_manager.execute_query(query, (symbol, exp_date))
    
    @staticmethod
    async def get_live_market_data(symbol: str) -> Optional[Dict]:
        """Fetch live market data from Polygon"""
        if not os.getenv('POLYGON_API_KEY'):
            return None
        
        try:
            # Get current stock price
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev"
            response = await http_client.get(url)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('results'):
                    result = data['results'][0]
                    return {
                        'symbol': symbol,
                        'price': result.get('c'),  # close price
                        'change': result.get('c', 0) - result.get('o', 0),
                        'change_percent': ((result.get('c', 0) - result.get('o', 0)) / result.get('o', 1)) * 100,
                        'volume': result.get('v'),
                        'timestamp': datetime.now()
                    }
        except Exception as e:
            logger.warning("Live data fetch failed", symbol=symbol, error=str(e))
        
        return None

# Dependency injection
async def get_em_service() -> ExpectedMoveService:
    return ExpectedMoveService()

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    services = {}
    
    # Check DuckDB
    try:
        await duckdb_manager.execute_query("SELECT 1")
        services["database"] = "healthy"
    except Exception:
        services["database"] = "unhealthy"
    
    # Check Redis
    try:
        await redis_client.ping()
        services["redis"] = "healthy"
    except Exception:
        services["redis"] = "unhealthy"
    
    # Check Polygon API
    services["polygon"] = "configured" if os.getenv('POLYGON_API_KEY') else "not_configured"
    
    status = "healthy" if all(s in ["healthy", "configured"] for s in services.values()) else "degraded"
    
    return HealthResponse(
        status=status,
        timestamp=datetime.now(),
        services=services
    )

@app.post("/api/expected-move", response_model=ExpectedMoveResponse)
async def get_expected_move(
    request: ExpectedMoveRequest,
    em_service: ExpectedMoveService = Depends(get_em_service)
):
    """Get expected move forecasts for a symbol"""
    symbol = request.symbol.upper()
    
    logger.info("Expected move request", symbol=symbol, horizons=request.horizons)
    
    # Check cache first
    cached = await em_service.get_cached_forecast(symbol, request.horizons)
    if cached:
        logger.info("Returning cached forecast", symbol=symbol)
        return ExpectedMoveResponse(**cached)
    
    # Get ML forecasts from DuckDB
    forecasts = await em_service.get_latest_forecasts(symbol, request.horizons)
    
    if not forecasts:
        raise HTTPException(
            status_code=404, 
            detail=f"No forecasts found for {symbol}"
        )
    
    # Get live market data if requested
    live_data = None
    if request.include_live:
        live_data = await em_service.get_live_market_data(symbol)
    
    # Prepare response
    response_data = {
        "symbol": symbol,
        "timestamp": datetime.now(),
        "forecasts": forecasts,
        "live_data": live_data,
        "metadata": {
            "forecast_count": len(forecasts),
            "horizons_requested": request.horizons,
            "has_live_data": live_data is not None,
            "backend": "duckdb"
        }
    }
    
    # Cache the response
    await em_service.cache_forecast(symbol, request.horizons, response_data)
    
    return ExpectedMoveResponse(**response_data)

def _parse_window_to_days(window: str) -> int:
    """Parse window strings like '90d' into integer days; default to 90 if invalid."""
    try:
        w = window.strip().lower()
        if w.endswith('d'):
            return max(1, int(w[:-1]))
        return max(1, int(w))
    except Exception:
        return 90

@app.get("/em/forecast", response_model=EmForecastLatestResponse)
async def em_forecast(symbol: str, exp: str):
    """Latest baseline EM record for (symbol, exp). Horizon fixed to 'to_exp' for MVP."""
    sym = symbol.upper()
    try:
        exp_date = date.fromisoformat(exp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid exp date; use YYYY-MM-DD")

    # Cache key
    cache_key = f"em:forecast:{sym}:{exp_date.isoformat()}"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            return EmForecastLatestResponse(**data)
    except Exception as e:
        logger.warning("EM forecast cache read failed", error=str(e))

    rec = await ExpectedMoveService.get_latest_for_symbol_exp(sym, exp_date)
    if not rec:
        raise HTTPException(status_code=404, detail="No forecast found")

    payload = {
        "symbol": sym,
        "exp": exp_date,
        "quote_ts": rec["quote_ts"],
        "horizon": rec["horizon"],
        "em_baseline": rec.get("em_baseline"),
        "band68_low": rec.get("band68_low"),
        "band68_high": rec.get("band68_high"),
        "band95_low": rec.get("band95_low"),
        "band95_high": rec.get("band95_high"),
        "metadata": {"source": "em_forecasts", "cache": False, "backend": "duckdb"},
    }

    try:
        await redis_client.setex(cache_key, 600, json.dumps(payload, default=str))  # 10 min
    except Exception as e:
        logger.warning("EM forecast cache write failed", error=str(e))

    return EmForecastLatestResponse(**payload)

@app.get("/em/history", response_model=EmHistoryResponse)
async def em_history(symbol: str, exp: str, window: str = "90d"):
    """Timeseries for baseline EM for charting. Window like '90d'."""
    sym = symbol.upper()
    try:
        exp_date = date.fromisoformat(exp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid exp date; use YYYY-MM-DD")
    days = _parse_window_to_days(window)

    cache_key = f"em:history:{sym}:{exp_date.isoformat()}:{days}d"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            return EmHistoryResponse(**data)
    except Exception as e:
        logger.warning("EM history cache read failed", error=str(e))

    rows = await ExpectedMoveService.get_history_for_symbol_exp(sym, exp_date, days)
    items = [
        {
            "quote_ts": r["quote_ts"],
            "em_baseline": r.get("em_baseline"),
            "band68_low": r.get("band68_low"),
            "band68_high": r.get("band68_high"),
            "band95_low": r.get("band95_low"),
            "band95_high": r.get("band95_high"),
        }
        for r in rows
    ]

    payload = {
        "symbol": sym,
        "exp": exp_date,
        "window": f"{days}d",
        "items": items,
        "metadata": {"count": len(items), "source": "em_forecasts", "cache": False, "backend": "duckdb"},
    }

    try:
        await redis_client.setex(cache_key, 600, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("EM history cache write failed", error=str(e))

    return EmHistoryResponse(**payload)

@app.get("/em/expiries", response_model=EmExpiriesResponse)
async def em_expiries(symbol: str, window: str = "120d"):
    """List upcoming expiries with forecasts for a symbol within a window (default 120d)."""
    sym = symbol.upper()
    days = _parse_window_to_days(window)

    cache_key = f"em:expiries:{sym}:{days}d"
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            return EmExpiriesResponse(**data)
    except Exception as e:
        logger.warning("EM expiries cache read failed", error=str(e))

    query = f"""
    SELECT DISTINCT exp_date
    FROM em_forecasts
    WHERE underlying = ?
      AND exp_date >= current_date
      AND exp_date <= (current_date + INTERVAL {days} DAY)
    ORDER BY exp_date ASC
    LIMIT 50
    """
    
    rows = await duckdb_manager.execute_query(query, (sym,))
    expiries = [r["exp_date"] for r in rows]

    payload = {
        "symbol": sym,
        "expiries": expiries,
        "metadata": {"count": len(expiries), "source": "em_forecasts", "cache": False, "backend": "duckdb"},
    }

    try:
        await redis_client.setex(cache_key, 600, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("EM expiries cache write failed", error=str(e))

    return EmExpiriesResponse(**payload)

@app.get("/api/symbols")
async def get_available_symbols():
    """Get list of symbols with available forecasts"""
    query = """
    SELECT DISTINCT underlying as symbol, COUNT(*) as forecast_count
    FROM em_forecasts
    WHERE quote_ts >= current_timestamp - INTERVAL 7 DAY
    GROUP BY underlying
    ORDER BY forecast_count DESC, underlying
    LIMIT 100
    """
    
    rows = await duckdb_manager.execute_query(query)
    return [{"symbol": row["symbol"], "forecast_count": row["forecast_count"]} for row in rows]

@app.get("/api/symbols/{symbol}/history")
async def get_symbol_history(symbol: str, days: int = 30):
    """Get historical forecasts for a symbol"""
    symbol = symbol.upper()
    
    query = f"""
    SELECT 
        quote_ts,
        horizon,
        em_baseline,
        band68_low,
        band68_high
    FROM em_forecasts
    WHERE underlying = ? 
      AND quote_ts >= current_timestamp - INTERVAL {days} DAY
    ORDER BY quote_ts DESC, horizon
    LIMIT 1000
    """
    
    rows = await duckdb_manager.execute_query(query, (symbol,))
    return rows

# DuckDB-specific endpoints
@app.get("/api/analytics/options-summary")
async def get_options_summary(symbol: str, days: int = 30):
    """Get options analytics summary using DuckDB views"""
    symbol = symbol.upper()
    
    query = f"""
    SELECT 
        date,
        avg_iv,
        median_iv,
        option_count
    FROM daily_iv_summary
    WHERE act_symbol = ?
      AND date >= current_date - INTERVAL {days} DAY
    ORDER BY date DESC
    LIMIT 100
    """
    
    rows = await duckdb_manager.execute_query(query, (symbol,))
    return rows

@app.get("/api/analytics/recent-options")
async def get_recent_options(symbol: Optional[str] = None, limit: int = 1000):
    """Get recent options data from materialized table"""
    query = """
    SELECT act_symbol, date, strike, call_put, vol, delta, gamma
    FROM recent_options
    """
    params = ()
    
    if symbol:
        query += " WHERE act_symbol = ?"
        params = (symbol.upper(),)
    
    query += f" ORDER BY date DESC, act_symbol LIMIT {limit}"
    
    rows = await duckdb_manager.execute_query(query, params)
    return rows

# Background task for model updates
@app.post("/api/admin/refresh-forecasts")
async def refresh_forecasts(background_tasks: BackgroundTasks):
    """Trigger forecast refresh (admin endpoint)"""
    
    async def refresh_task():
        logger.info("Starting forecast refresh...")
        try:
            # Clear relevant caches
            pattern = "em_forecast:*"
            keys = await redis_client.keys(pattern)
            if keys:
                await redis_client.delete(*keys)
            
            # Refresh materialized tables in DuckDB
            await duckdb_manager.execute_query("DROP TABLE IF EXISTS recent_options")
            await duckdb_manager.execute_query("""
                CREATE TABLE recent_options AS
                SELECT * 
                FROM options_chain 
                WHERE date >= (SELECT MAX(date) - INTERVAL 30 DAYS FROM options_chain)
            """)
            
            logger.info("Forecast cache and tables refreshed", keys_cleared=len(keys))
        except Exception as e:
            logger.error("Refresh failed", error=str(e))
    
    background_tasks.add_task(refresh_task)
    return {"message": "Forecast refresh initiated", "backend": "duckdb"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend_duckdb:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["default"],
            },
        }
    )
