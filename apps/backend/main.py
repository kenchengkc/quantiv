#!/usr/bin/env python3
"""
Quantiv FastAPI Backend - Expected Move Forecasting API
Serves ML-generated expected moves with live market data integration
"""

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import asyncpg
import redis.asyncio as redis
import httpx
import structlog
from datetime import datetime, date, timedelta
import os
from contextlib import asynccontextmanager
import json
from pathlib import Path
from dotenv import load_dotenv

# Configure structured logging
logger = structlog.get_logger()

# Load environment variables from repo-level config/.env.* when available.
# In containers, envs are injected by docker-compose via env_file/environment, so this is best-effort only.
try:
    repo_root = Path(__file__).resolve().parents[2]
except IndexError:
    # Shallow directory (e.g., container at /app); fall back to file's parent
    repo_root = Path(__file__).resolve().parent
env_file = ".env.production" if os.getenv("NODE_ENV") == "production" or os.getenv("ENVIRONMENT") == "production" else ".env.local"
env_path = repo_root / "config" / env_file
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# Pydantic models
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

# Global connections
db_pool: asyncpg.Pool = None
redis_client: redis.Redis = None
http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global db_pool, redis_client, http_client
    
    logger.info("🚀 Starting Quantiv API...")
    
    # Initialize database pool (supports DATABASE_URL or discrete params)
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        logger.info("Connecting to Postgres via DATABASE_URL")
        db_pool = await asyncpg.create_pool(
            dsn=db_url,
            min_size=5,
            max_size=20,
        )
    else:
        logger.info("Connecting to Postgres via discrete env vars")
        db_pool = await asyncpg.create_pool(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "quantiv_user"),
            password=os.getenv("POSTGRES_PASSWORD", "quantiv_secure_2024"),
            database=os.getenv("POSTGRES_DB", "quantiv_options"),
            min_size=5,
            max_size=20,
        )
    
    # Initialize Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    # Initialize HTTP client for Polygon
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"Authorization": f"Bearer {os.getenv('POLYGON_API_KEY', '')}"}
    )
    
    logger.info("✅ Services initialized")
    
    yield
    
    # Cleanup
    logger.info("🔄 Shutting down services...")
    await db_pool.close()
    await redis_client.aclose()
    await http_client.aclose()

# Create FastAPI app
app = FastAPI(
    title="Quantiv Expected Move API",
    description="ML-powered options expected move forecasting",
    version="2.0.0",
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
        """Get latest ML forecasts from PostgreSQL"""
        query = """
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
        WHERE underlying = $1 
          AND horizon = ANY($2)
          AND quote_ts >= NOW() - INTERVAL '1 day'
        ORDER BY quote_ts DESC, exp_date ASC
        LIMIT 50
        """
        
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, horizons)
            
        return [dict(row) for row in rows]

    @staticmethod
    async def get_latest_for_symbol_exp(symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        """Get the latest forecast for a symbol and exp_date (MVP horizon 'to_exp')."""
        query = """
        SELECT underlying, quote_ts, exp_date, horizon,
               em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = $1 AND exp_date = $2 AND horizon = 'to_exp'
        ORDER BY quote_ts DESC
        LIMIT 1
        """
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(query, symbol, exp_date)
        return dict(row) if row else None

    @staticmethod
    async def get_history_for_symbol_exp(symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        """Get timeseries for a symbol/exp_date over a window (days)."""
        query = """
        SELECT quote_ts, em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = $1 AND exp_date = $2 AND horizon = 'to_exp'
          AND quote_ts >= NOW() - ($3::text || ' days')::interval
        ORDER BY quote_ts ASC
        """
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, exp_date, str(window_days))
        return [dict(r) for r in rows]
    
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
    
    # Check database
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
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
    
    # Get ML forecasts from database
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
            "has_live_data": live_data is not None
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
        "metadata": {"source": "em_forecasts", "cache": False},
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
        "metadata": {"count": len(items), "source": "em_forecasts", "cache": False},
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

    query = """
    SELECT DISTINCT exp_date
    FROM em_forecasts
    WHERE underlying = $1
      AND exp_date >= CURRENT_DATE
      AND exp_date <= (CURRENT_DATE + ($2::text || ' days')::interval)
    ORDER BY exp_date ASC
    LIMIT 50
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, sym, str(days))
    expiries = [r["exp_date"] for r in rows]

    payload = {
        "symbol": sym,
        "expiries": expiries,
        "metadata": {"count": len(expiries), "source": "em_forecasts", "cache": False},
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
    WHERE quote_ts >= NOW() - INTERVAL '7 days'
    GROUP BY underlying
    ORDER BY forecast_count DESC, underlying
    LIMIT 100
    """
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query)
    
    return [{"symbol": row["symbol"], "forecast_count": row["forecast_count"]} for row in rows]

@app.get("/api/symbols/{symbol}/history")
async def get_symbol_history(symbol: str, days: int = 30):
    """Get historical forecasts for a symbol"""
    symbol = symbol.upper()
    
    query = """
    SELECT 
        quote_ts,
        horizon,
        em_baseline,
        band68_low,
        band68_high
    FROM em_forecasts
    WHERE underlying = $1 
      AND quote_ts >= NOW() - INTERVAL '%s days'
    ORDER BY quote_ts DESC, horizon
    LIMIT 1000
    """ % days
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, symbol)
    
    return [dict(row) for row in rows]

# Background task for model updates
@app.post("/api/admin/refresh-forecasts")
async def refresh_forecasts(background_tasks: BackgroundTasks):
    """Trigger forecast refresh (admin endpoint)"""
    
    async def refresh_task():
        logger.info("Starting forecast refresh...")
        # This would trigger the batch ML pipeline
        # For now, just clear relevant caches
        try:
            pattern = "em_forecast:*"
            keys = await redis_client.keys(pattern)
            if keys:
                await redis_client.delete(*keys)
            logger.info("Forecast cache cleared", keys_cleared=len(keys))
        except Exception as e:
            logger.error("Cache clear failed", error=str(e))
    
    background_tasks.add_task(refresh_task)
    return {"message": "Forecast refresh initiated"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
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
