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
import duckdb
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
data_backend: "DataBackend" = None
duckdb_conn: Optional[duckdb.DuckDBPyConnection] = None
DATA_BACKEND_MODE: str = "postgres"  # postgres | duckdb | hybrid

class DataBackend:
    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        raise NotImplementedError
    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def health(self) -> Dict[str, str]:
        return {"database": "unknown"}

class PostgresBackend(DataBackend):
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
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
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, horizons)
        return [dict(row) for row in rows]

    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        query = """
        SELECT underlying, quote_ts, exp_date, horizon,
               em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = $1 AND exp_date = $2 AND horizon = 'to_exp'
        ORDER BY quote_ts DESC
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, symbol, exp_date)
        return dict(row) if row else None

    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        query = """
        SELECT quote_ts, em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = $1 AND exp_date = $2 AND horizon = 'to_exp'
          AND quote_ts >= NOW() - ($3::text || ' days')::interval
        ORDER BY quote_ts ASC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, exp_date, str(window_days))
        return [dict(r) for r in rows]

    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        query = """
        SELECT DISTINCT exp_date
        FROM em_forecasts
        WHERE underlying = $1
          AND exp_date >= CURRENT_DATE
          AND exp_date <= (CURRENT_DATE + ($2::text || ' days')::interval)
        ORDER BY exp_date ASC
        LIMIT 50
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, str(days))
        return [r["exp_date"] for r in rows]

    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        query = """
        SELECT DISTINCT underlying as symbol, COUNT(*) as forecast_count
        FROM em_forecasts
        WHERE quote_ts >= NOW() - INTERVAL '$1 days'
        GROUP BY underlying
        ORDER BY forecast_count DESC, underlying
        LIMIT 100
        """
        # Use param injection for days via format to keep same style as existing code
        q = query.replace("$1", str(days))
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(q)
        return [{"symbol": row["symbol"], "forecast_count": row["forecast_count"]} for row in rows]

    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
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
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol)
        return [dict(row) for row in rows]

    async def health(self) -> Dict[str, str]:
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return {"postgres": "healthy"}
        except Exception:
            return {"postgres": "unhealthy"}

class DuckDBBackend(DataBackend):
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    def _fetch_df(self, sql: str, params: Optional[List[Any]] = None):
        if params is None:
            return self.conn.execute(sql).fetchdf()
        return self.conn.execute(sql, params).fetchdf()

    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        sql = (
            "SELECT underlying, quote_ts, exp_date, horizon, em_baseline, band68_low, band68_high, band95_low, band95_high "
            "FROM em_forecasts "
            "WHERE underlying = ? AND quote_ts >= now() - INTERVAL 1 DAY "
            "ORDER BY quote_ts DESC, exp_date ASC LIMIT 200"
        )
        df = self._fetch_df(sql, [symbol])
        if df.empty:
            return []
        if horizons:
            df = df[df["horizon"].isin(horizons)]
        return df.head(50).to_dict(orient="records")

    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT underlying, quote_ts, exp_date, horizon, em_baseline, band68_low, band68_high, band95_low, band95_high "
            "FROM em_forecasts WHERE underlying = ? AND exp_date = ? AND horizon = 'to_exp' "
            "ORDER BY quote_ts DESC LIMIT 1"
        )
        df = self._fetch_df(sql, [symbol, exp_date])
        return df.to_dict(orient="records")[0] if not df.empty else None

    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        sql = (
            "SELECT quote_ts, em_baseline, band68_low, band68_high, band95_low, band95_high "
            "FROM em_forecasts WHERE underlying = ? AND exp_date = ? AND horizon = 'to_exp' "
            f"AND quote_ts >= now() - INTERVAL {max(1, int(window_days))} DAY "
            "ORDER BY quote_ts ASC"
        )
        df = self._fetch_df(sql, [symbol, exp_date])
        return df.to_dict(orient="records") if not df.empty else []

    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        sql = (
            "SELECT DISTINCT exp_date FROM em_forecasts WHERE underlying = ? "
            f"AND exp_date BETWEEN current_date AND current_date + INTERVAL {max(1, int(days))} DAY "
            "ORDER BY exp_date ASC LIMIT 50"
        )
        df = self._fetch_df(sql, [symbol])
        if df.empty:
            return []
        vals = df["exp_date"].tolist()
        out: List[date] = []
        for v in vals:
            if isinstance(v, date):
                out.append(v)
            else:
                try:
                    out.append(date.fromisoformat(str(v)))
                except Exception:
                    continue
        return out

    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        sql = (
            "SELECT underlying as symbol, COUNT(*) as forecast_count FROM em_forecasts "
            f"WHERE quote_ts >= now() - INTERVAL {max(1, int(days))} DAY "
            "GROUP BY underlying ORDER BY forecast_count DESC, underlying LIMIT 100"
        )
        df = self._fetch_df(sql)
        return [] if df.empty else df.to_dict(orient="records")

    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        sql = (
            "SELECT quote_ts, horizon, em_baseline, band68_low, band68_high FROM em_forecasts "
            "WHERE underlying = ? "
            f"AND quote_ts >= now() - INTERVAL {max(1, int(days))} DAY "
            "ORDER BY quote_ts DESC, horizon LIMIT 1000"
        )
        df = self._fetch_df(sql, [symbol])
        return [] if df.empty else df.to_dict(orient="records")

    async def health(self) -> Dict[str, str]:
        try:
            _ = self.conn.execute("SELECT 1").fetchone()
            return {"duckdb": "healthy"}
        except Exception:
            return {"duckdb": "unhealthy"}

class HybridBackend(DataBackend):
    def __init__(self, duck: DuckDBBackend, pg: PostgresBackend, last_days: int = 1):
        self.duck = duck
        self.pg = pg
        self.last_days = max(1, int(last_days))

    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        d = await self.duck.get_latest_forecasts(symbol, horizons)
        p = await self.pg.get_latest_forecasts(symbol, horizons)
        # Deduplicate by key
        seen = set()
        out = []
        for rec in d + p:
            key = (rec.get("quote_ts"), rec.get("exp_date"), rec.get("horizon"))
            if key not in seen:
                seen.add(key)
                out.append(rec)
        out.sort(key=lambda r: (r.get("quote_ts"), r.get("exp_date")), reverse=True)
        return out[:50]

    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        d = await self.duck.get_latest_for_symbol_exp(symbol, exp_date)
        p = await self.pg.get_latest_for_symbol_exp(symbol, exp_date)
        if d and p:
            return d if d["quote_ts"] >= p["quote_ts"] else p
        return d or p

    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        d = await self.duck.get_history_for_symbol_exp(symbol, exp_date, window_days)
        p = await self.pg.get_history_for_symbol_exp(symbol, exp_date, min(self.last_days, window_days))
        seen = set()
        out = []
        for rec in d + p:
            key = rec.get("quote_ts")
            if key not in seen:
                seen.add(key)
                out.append(rec)
        out.sort(key=lambda r: r.get("quote_ts"))
        return out

    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        ds = set(await self.duck.get_expiries(symbol, days))
        ps = set(await self.pg.get_expiries(symbol, days))
        return sorted(list(ds | ps))[:50]

    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        ds = await self.duck.get_symbols(days)
        ps = await self.pg.get_symbols(days)
        agg: Dict[str, int] = {}
        for rec in ds + ps:
            agg[rec["symbol"]] = agg.get(rec["symbol"], 0) + int(rec.get("forecast_count", 0))
        out = [{"symbol": k, "forecast_count": v} for k, v in agg.items()]
        out.sort(key=lambda r: (r["forecast_count"], r["symbol"]), reverse=True)
        return out[:100]

    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        d = await self.duck.get_symbol_history_all_horizons(symbol, days)
        p = await self.pg.get_symbol_history_all_horizons(symbol, min(self.last_days, days))
        seen = set()
        out = []
        for rec in d + p:
            key = (rec.get("quote_ts"), rec.get("horizon"))
            if key not in seen:
                seen.add(key)
                out.append(rec)
        out.sort(key=lambda r: (r.get("quote_ts"), r.get("horizon")), reverse=True)
        return out[:1000]

    async def health(self) -> Dict[str, str]:
        h = {}
        h.update(await self.duck.health())
        h.update(await self.pg.health())
        return h

def _ensure_duckdb_em_view(conn: duckdb.DuckDBPyConnection, data_dir: str):
    """Create or replace the em_forecasts view to point at Parquet under data_dir.
    This prevents absolute host paths inside the .duckdb file from breaking inside containers.
    """
    try:
        parquet_path = str((Path(data_dir) / "forecasts" / "em_forecasts.parquet").resolve())
        # Use CREATE OR REPLACE to override any stale definitions
        conn.execute(
            f"""
            CREATE OR REPLACE VIEW em_forecasts AS
            SELECT * FROM read_parquet('{parquet_path}')
            """
        )
        logger.info("Ensured DuckDB em_forecasts view", path=parquet_path)
    except Exception as e:
        logger.warning("Failed to ensure em_forecasts view", error=str(e))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global db_pool, redis_client, http_client, data_backend, duckdb_conn, DATA_BACKEND_MODE
    
    logger.info("🚀 Starting Quantiv API...")
    
    DATA_BACKEND_MODE = os.getenv("DATA_BACKEND", "postgres").lower()
    use_pg = DATA_BACKEND_MODE in ("postgres", "hybrid")
    use_duck = DATA_BACKEND_MODE in ("duckdb", "hybrid")

    # Initialize databases as configured
    pg_ready = False
    if use_pg:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            logger.info("Connecting to Postgres via DATABASE_URL")
            db_pool = await asyncpg.create_pool(dsn=db_url, min_size=2, max_size=10)
        else:
            logger.info("Connecting to Postgres via discrete env vars")
            db_pool = await asyncpg.create_pool(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=int(os.getenv("POSTGRES_PORT", "5432")),
                user=os.getenv("POSTGRES_USER", "quantiv_user"),
                password=os.getenv("POSTGRES_PASSWORD", "quantiv_secure_2024"),
                database=os.getenv("POSTGRES_DB", "quantiv_options"),
                min_size=2,
                max_size=10,
            )
        pg_ready = True

    duck_ready = False
    if use_duck:
        duck_path = os.getenv("DUCKDB_PATH", "./quantiv.duckdb")
        logger.info("Connecting to DuckDB", path=duck_path)
        duckdb_conn = duckdb.connect(duck_path, read_only=False)
        try:
            duckdb_conn.execute("INSTALL parquet")
            duckdb_conn.execute("LOAD parquet")
        except Exception:
            # parquet is often built-in; ignore errors here
            pass
        # Ensure em_forecasts view points at container-mounted parquet
        _ensure_duckdb_em_view(duckdb_conn, os.getenv("DATA_DIR", "./data"))
        duck_ready = True

    # Select backend
    if DATA_BACKEND_MODE == "postgres":
        data_backend = PostgresBackend(db_pool)
    elif DATA_BACKEND_MODE == "duckdb":
        data_backend = DuckDBBackend(duckdb_conn)
    else:
        data_backend = HybridBackend(DuckDBBackend(duckdb_conn), PostgresBackend(db_pool), int(os.getenv("HYBRID_LAST_DAYS", "1")))
    
    # Initialize Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    # Initialize HTTP client for Polygon
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"Authorization": f"Bearer {os.getenv('POLYGON_API_KEY', '')}"}
    )
    
    logger.info("✅ Services initialized", backend=DATA_BACKEND_MODE, postgres=pg_ready, duckdb=duck_ready)
    
    yield
    
    # Cleanup
    logger.info("🔄 Shutting down services...")
    try:
        if use_pg and db_pool:
            await db_pool.close()
    finally:
        pass
    try:
        await redis_client.aclose()
    finally:
        pass
    try:
        await http_client.aclose()
    finally:
        pass
    try:
        if use_duck and duckdb_conn:
            duckdb_conn.close()
    finally:
        pass

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
        """Get latest ML forecasts from active backend"""
        return await data_backend.get_latest_forecasts(symbol, horizons)

    @staticmethod
    async def get_latest_for_symbol_exp(symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        """Get the latest forecast for a symbol and exp_date (MVP horizon 'to_exp')."""
        return await data_backend.get_latest_for_symbol_exp(symbol, exp_date)

    @staticmethod
    async def get_history_for_symbol_exp(symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        """Get timeseries for a symbol/exp_date over a window (days)."""
        return await data_backend.get_history_for_symbol_exp(symbol, exp_date, window_days)
    
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
    # Check active data backend(s)
    backend = DATA_BACKEND_MODE
    if backend in ("postgres", "hybrid"):
        try:
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            services["postgres"] = "healthy"
        except Exception:
            services["postgres"] = "unhealthy"
    if backend in ("duckdb", "hybrid"):
        try:
            _ = duckdb_conn.execute("SELECT 1").fetchone()
            services["duckdb"] = "healthy"
        except Exception:
            services["duckdb"] = "unhealthy"
    
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

    # Use pluggable backend (DuckDB/Postgres/Hybrid)
    expiries = await data_backend.get_expiries(sym, days)

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
    rows = await data_backend.get_symbols(7)
    return rows

@app.get("/api/symbols/{symbol}/history")
async def get_symbol_history(symbol: str, days: int = 30):
    """Get historical forecasts for a symbol"""
    symbol = symbol.upper()
    rows = await data_backend.get_symbol_history_all_horizons(symbol, days)
    return rows

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
