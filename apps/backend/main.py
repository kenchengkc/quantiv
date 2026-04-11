#!/usr/bin/env python3
"""
Quantiv FastAPI Backend - Expected Move Forecasting API
Slim entrypoint: wires up middleware, lifespan, and routers.
Models live in models/, backends in backends/, routes in routers/.
"""

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import List, Optional
import asyncpg
import redis.asyncio as redis
import httpx
import structlog
import duckdb
import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from services.ml_service import MLService
from backends import PostgresBackend, DuckDBBackend, HybridBackend, DataBackend
from routers.em import router as em_router, init_router as init_em_router

# Configure structured logging
logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------
try:
    repo_root = Path(__file__).resolve().parents[2]
except IndexError:
    repo_root = Path(__file__).resolve().parent
env_file = ".env.production" if os.getenv("NODE_ENV") == "production" or os.getenv("ENVIRONMENT") == "production" else ".env.local"
env_path = repo_root / "config" / env_file
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# ---------------------------------------------------------------------------
# Global connections (populated in lifespan)
# ---------------------------------------------------------------------------
db_pool: asyncpg.Pool = None
redis_client: redis.Redis = None
http_client: httpx.AsyncClient = None
data_backend: DataBackend = None
duckdb_conn: Optional[duckdb.DuckDBPyConnection] = None
ml_service: Optional[MLService] = None
DATA_BACKEND_MODE: str = "postgres"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_duckdb_em_view(conn: duckdb.DuckDBPyConnection, data_dir: str):
    """Create or replace the em_forecasts view to point at Parquet under data_dir."""
    try:
        parquet_path = str((Path(data_dir) / "forecasts" / "em_forecasts.parquet").resolve())
        conn.execute(
            f"CREATE OR REPLACE VIEW em_forecasts AS SELECT * FROM read_parquet('{parquet_path}')"
        )
        logger.info("Ensured DuckDB em_forecasts view", path=parquet_path)
    except Exception as e:
        logger.warning("Failed to ensure em_forecasts view", error=str(e))


def _validate_env():
    """Validate required environment variables before startup."""
    errors: List[str] = []
    warnings: List[str] = []

    backend = os.getenv("DATA_BACKEND", "postgres").lower()
    use_pg = backend in ("postgres", "hybrid")
    use_duck = backend in ("duckdb", "hybrid")

    if use_pg:
        has_url = bool(os.getenv("DATABASE_URL"))
        has_discrete = bool(os.getenv("POSTGRES_USER")) and bool(os.getenv("POSTGRES_PASSWORD"))
        if not has_url and not has_discrete:
            errors.append("Postgres backend requires DATABASE_URL or both POSTGRES_USER and POSTGRES_PASSWORD")

    if use_duck:
        duck_path = os.getenv("DUCKDB_PATH", "./quantiv.duckdb")
        if not Path(duck_path).exists():
            warnings.append(f"DUCKDB_PATH '{duck_path}' does not exist yet")

    if not os.getenv("REDIS_URL"):
        warnings.append("REDIS_URL not set — caching will connect to localhost:6379")
    if not os.getenv("POLYGON_API_KEY"):
        warnings.append("POLYGON_API_KEY not set — live market data unavailable")
    if not os.getenv("ADMIN_API_KEY"):
        warnings.append("ADMIN_API_KEY not set — admin endpoints will return 503")

    for w in warnings:
        logger.warning("Env config warning", detail=w)
    if errors:
        for e in errors:
            logger.error("Env config error", detail=e)
        raise RuntimeError("Missing required environment variables: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client, http_client, data_backend, duckdb_conn, ml_service, DATA_BACKEND_MODE

    logger.info("🚀 Starting Quantiv API...")
    _validate_env()

    DATA_BACKEND_MODE = os.getenv("DATA_BACKEND", "postgres").lower()
    use_pg = DATA_BACKEND_MODE in ("postgres", "hybrid")
    use_duck = DATA_BACKEND_MODE in ("duckdb", "hybrid")

    # -- Postgres --------------------------------------------------------
    pg_ready = False
    if use_pg:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            logger.info("Connecting to Postgres via DATABASE_URL")
            db_pool = await asyncpg.create_pool(dsn=db_url, min_size=2, max_size=10)
        else:
            pg_user = os.getenv("POSTGRES_USER")
            pg_password = os.getenv("POSTGRES_PASSWORD")
            if not pg_user or not pg_password:
                raise RuntimeError("POSTGRES_USER and POSTGRES_PASSWORD must be set when DATABASE_URL is not provided")
            db_pool = await asyncpg.create_pool(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=int(os.getenv("POSTGRES_PORT", "5432")),
                user=pg_user, password=pg_password,
                database=os.getenv("POSTGRES_DB", "quantiv_options"),
                min_size=2, max_size=10,
            )
        pg_ready = True

    # -- DuckDB ----------------------------------------------------------
    duck_ready = False
    if use_duck:
        duck_path = os.getenv("DUCKDB_PATH", "./quantiv.duckdb")
        logger.info("Connecting to DuckDB", path=duck_path)
        duckdb_conn = duckdb.connect(duck_path, read_only=False)
        try:
            duckdb_conn.execute("INSTALL parquet")
            duckdb_conn.execute("LOAD parquet")
        except Exception:
            pass
        _ensure_duckdb_em_view(duckdb_conn, os.getenv("DATA_DIR", "./data"))
        duck_ready = True

    # -- Backend selection -----------------------------------------------
    if DATA_BACKEND_MODE == "postgres":
        data_backend = PostgresBackend(db_pool)
    elif DATA_BACKEND_MODE == "duckdb":
        data_backend = DuckDBBackend(duckdb_conn)
    else:
        data_backend = HybridBackend(DuckDBBackend(duckdb_conn), PostgresBackend(db_pool),
                                     int(os.getenv("HYBRID_LAST_DAYS", "1")))

    # -- Redis -----------------------------------------------------------
    redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

    # -- HTTP client (Polygon) -------------------------------------------
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"Authorization": f"Bearer {os.getenv('POLYGON_API_KEY', '')}"},
    )

    # -- ML service ------------------------------------------------------
    if use_duck:
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        try:
            ml_service = MLService(data_dir, data_dir / "quantiv.duckdb")
            logger.info("✅ ML service initialized", available_symbols=len(ml_service.get_available_symbols()))
        except Exception as e:
            logger.warning("Failed to initialize ML service", error=str(e))

    logger.info("✅ Services initialized", backend=DATA_BACKEND_MODE, postgres=pg_ready, duckdb=duck_ready, ml_ready=ml_service is not None)

    # Wire up router shared state
    init_em_router({
        "data_backend": data_backend, "redis_client": redis_client,
        "http_client": http_client, "ml_service": ml_service,
        "db_pool": db_pool, "duckdb_conn": duckdb_conn,
        "DATA_BACKEND_MODE": DATA_BACKEND_MODE,
    })

    yield

    # -- Cleanup ---------------------------------------------------------
    logger.info("🔄 Shutting down services...")
    for cleanup in [
        lambda: db_pool.close() if use_pg and db_pool else None,
        lambda: redis_client.aclose(),
        lambda: http_client.aclose(),
        lambda: duckdb_conn.close() if use_duck and duckdb_conn else None,
        lambda: ml_service.close() if ml_service else None,
    ]:
        try:
            result = cleanup()
            if result and hasattr(result, "__await__"):
                await result
        except Exception:
            pass


# ---------------------------------------------------------------------------
# App & middleware
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Quantiv Expected Move API",
    description="ML-powered options expected move forecasting",
    version="2.0.0",
    lifespan=lifespan,
)

allowed_origins = ["http://localhost:3000", "http://localhost:3001", "https://quantiv.vercel.app"]
custom_domain = os.getenv("FRONTEND_URL")
if custom_domain:
    allowed_origins.append(custom_domain)

app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# Rate limiting — 60 requests/minute per IP (generous for 5-10 users)
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Admin security
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_admin_key(api_key: Optional[str] = Security(_api_key_header)) -> str:
    expected = os.getenv("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API key not configured on server")
    if not api_key or api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return api_key

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------
app.include_router(em_router)

# ---------------------------------------------------------------------------
# Admin endpoint (kept in main so it can reference verify_admin_key directly)
# ---------------------------------------------------------------------------
@app.post("/api/admin/refresh-forecasts")
async def refresh_forecasts(background_tasks: BackgroundTasks, _key: str = Depends(verify_admin_key)):
    """Trigger forecast refresh (admin endpoint)"""
    async def refresh_task():
        logger.info("Starting forecast refresh...")
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
