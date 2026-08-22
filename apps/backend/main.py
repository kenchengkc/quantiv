"""
Quantiv FastAPI Backend - Expected Move Forecasting API
Slim entrypoint: wires up middleware, lifespan, and routers.
Models live in models/, backends in backends/, routes in routers/.
"""

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import duckdb
import httpx
import redis.asyncio as redis
import structlog
from backends import DataBackend, DuckDBBackend, HybridBackend, PostgresBackend
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from middleware.hmac_auth import HmacAuthMiddleware
from routers.em import init_router as init_em_router
from routers.em import router as em_router
from routers.ml_predict import init_router as init_ml_predict_router
from routers.ml_predict import router as ml_predict_router
from services import predict_service, r2_models
from services.ml_service import MLService
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

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
duckdb_conn: duckdb.DuckDBPyConnection | None = None
ml_service: MLService | None = None
DATA_BACKEND_MODE: str = "postgres"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _env_flag(name: str, default: bool) -> bool:
    """Read a conventional boolean environment flag."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid boolean environment flag; using default", name=name)
    return default


def _is_production() -> bool:
    return (
        os.getenv("ENVIRONMENT", "").lower() in {"production", "prod"}
        or os.getenv("NODE_ENV", "").lower() == "production"
        or os.getenv("RAILWAY_ENVIRONMENT", "").lower() == "production"
        or os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower() == "production"
    )


def _ensure_duckdb_em_view(conn: duckdb.DuckDBPyConnection, data_dir: str):
    """Create or replace the em_forecasts view over the latest forecasts Parquet.

    The daily-score script writes `forecasts_<YYYY-MM-DD>.parquet` snapshots
    into `data_dir/forecasts/`. We point the view at the newest snapshot so
    the API serves the same numbers as the nightly batch. Falls back to the
    legacy single-file `em_forecasts.parquet` for older deploys.

    On a fresh Railway volume (or any deploy that hasn't pulled R2 yet),
    the forecasts directory is empty. In that case we log a warning and
    skip view creation rather than crashing startup. The first time
    `tools/build_frontend_data.py` or the Phase 2 lazy-fetch helper drops
    a parquet here, callers can re-invoke this function (or restart the
    service) to wire the view up.

    Until then, downstream queries against `em_forecasts` will fail with
    "Catalog Error: view does not exist" — that's by design. The
    alternative (a silently-empty view) hid an em_forecasts vs
    em_forecasts_view rename bug for months.
    """
    forecasts_dir = Path(data_dir) / "forecasts"
    candidates = sorted(forecasts_dir.glob("forecasts_*.parquet"), reverse=True)
    legacy = forecasts_dir / "em_forecasts.parquet"
    target = candidates[0] if candidates else (legacy if legacy.exists() else None)
    if target is None:
        logger.warning(
            "em_forecasts parquet not found — skipping view creation",
            forecasts_dir=str(forecasts_dir),
            remediation=(
                "Pull data/forecasts/ from R2, run scripts/daily_score.py, "
                "or wait for the Phase 2 lazy-fetch helper to populate it."
            ),
        )
        return
    parquet_path = str(target.resolve())
    conn.execute(
        f"CREATE OR REPLACE VIEW em_forecasts AS SELECT * FROM read_parquet('{parquet_path}')"
    )
    logger.info("Ensured DuckDB em_forecasts view", path=parquet_path)


def _validate_env():
    """Validate required environment variables before startup."""
    errors: list[str] = []
    warnings: list[str] = []

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
    if _is_production() and not os.getenv("BACKEND_SHARED_SECRET"):
        errors.append("BACKEND_SHARED_SECRET is required in production for HMAC-protected backend routes")

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
        # parquet support is built into duckdb >= 0.9, so no INSTALL/LOAD
        # needed. The explicit INSTALL parquet + LOAD parquet calls that
        # used to live here triggered "ImportError: cannot load module
        # more than once per process" on Railway because the wheel's
        # bundled extension would attempt a second register against the
        # already-registered builtin.
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
        except Exception as e:  # noqa: BLE001 - optional ML startup must degrade safely
            logger.warning("Failed to initialize ML service", error=str(e))

    # -- Pull serving models from R2 onto the mounted volume -------------
    # The weekly retrain in daily-refresh.yml writes fresh joblibs to R2.
    # Without this sync, every Railway deploy after a retrain serves the
    # baked-in models from the last Docker build until a new image is
    # pushed. The volume copy persists across restarts so subsequent boots
    # only download files that R2 has touched since.
    if r2_models.configured():
        models_volume = Path(os.getenv("DATA_DIR", "./data")) / "models"
        try:
            n = r2_models.sync_models_from_r2(models_volume)
            if n > 0 or any(models_volume.glob("lgbm_T*.joblib")):
                os.environ["ML_MODELS_DIR"] = str(models_volume)
                predict_service.reset_cache()
                logger.info(
                    "ML models resolved via volume", path=str(models_volume), synced=n,
                )
            else:
                logger.info("R2 sync wrote 0 files and volume is empty; using baked-in models")
        except Exception as e:  # noqa: BLE001 - retain baked-in models on any sync failure
            logger.warning("R2 model sync errored; using baked-in models", error=str(e))
    else:
        logger.info("R2 not configured; using image-baked models")

    logger.info("✅ Services initialized", backend=DATA_BACKEND_MODE, postgres=pg_ready, duckdb=duck_ready, ml_ready=ml_service is not None)

    # Wire up router shared state
    init_em_router({
        "data_backend": data_backend, "redis_client": redis_client,
        "http_client": http_client, "ml_service": ml_service,
        "db_pool": db_pool, "duckdb_conn": duckdb_conn,
        "DATA_BACKEND_MODE": DATA_BACKEND_MODE,
    })
    # Phase 1 re-inference router. Only needs Postgres (for the feature
    # snapshot) + Upstash (for the response cache).
    init_ml_predict_router({
        "db_pool": db_pool,
        "redis_client": redis_client,
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
        except Exception as exc:  # noqa: BLE001 - attempt every independent cleanup
            logger.warning("Service cleanup failed", error=str(exc))


# ---------------------------------------------------------------------------
# App & middleware
# ---------------------------------------------------------------------------
docs_enabled = _env_flag("DOCS_ENABLED", not _is_production())
app = FastAPI(
    title="Quantiv Expected Move API",
    description="ML-powered options expected move forecasting",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)

rate_limit_default = os.getenv("RATE_LIMIT_DEFAULT", "60/minute").strip() or "60/minute"
rate_limit_outage_fallback = (
    os.getenv("RATE_LIMIT_OUTAGE_FALLBACK", "1000000/minute").strip()
    or "1000000/minute"
)
rate_limit_storage = os.getenv("REDIS_URL") or "memory://"
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[rate_limit_default],
    storage_uri=rate_limit_storage,
    # SlowAPI's swallow_errors path still tries to inject missing request state.
    # A permissive in-memory fallback preserves fail-open availability while
    # retaining a finite emergency ceiling if Redis is unreachable.
    in_memory_fallback=[rate_limit_outage_fallback],
    swallow_errors=True,
    enabled=_env_flag("RATE_LIMIT_ENABLED", True),
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware executes in reverse registration order. Keep CORS outermost so
# rejected requests still receive browser-safe headers, then authenticate
# before charging authenticated traffic against the global rate limit.
app.add_middleware(SlowAPIMiddleware)
docs_exempt = (
    ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json")
    if docs_enabled
    else ()
)
app.add_middleware(HmacAuthMiddleware, extra_exempt=docs_exempt)

allowed_origins = ["http://localhost:3000", "http://localhost:3001", "https://quantiv.vercel.app"]
custom_domain = os.getenv("FRONTEND_URL")
if custom_domain:
    allowed_origins.append(custom_domain)

app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------------------
# Admin security
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_admin_key(api_key: str | None = Security(_api_key_header)) -> str:
    expected = os.getenv("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API key not configured on server")
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return api_key

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------
app.include_router(em_router)
app.include_router(ml_predict_router)

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
        except Exception as e:  # noqa: BLE001 - background admin task reports all failures
            logger.error("Cache clear failed", error=str(e))
    background_tasks.add_task(refresh_task)
    return {"message": "Forecast refresh initiated"}


@app.post("/api/admin/sync-models")
async def sync_models(_key: str = Depends(verify_admin_key)):
    """Force-pull the latest LightGBM models from R2 onto the volume,
    then reset the in-process model cache so subsequent predictions use
    the new files. Run this after the Sunday retrain lands fresh models
    in R2 — avoids waiting for the next Railway deploy.

    Returns the number of files actually written (size-mismatched) and
    the path predict_service is now reading from.
    """
    if not r2_models.configured():
        raise HTTPException(status_code=503, detail="R2 is not configured on this instance")
    models_volume = Path(os.getenv("DATA_DIR", "./data")) / "models"
    n = r2_models.sync_models_from_r2(models_volume)
    if any(models_volume.glob("lgbm_T*.joblib")):
        os.environ["ML_MODELS_DIR"] = str(models_volume)
    predict_service.reset_cache()
    return {
        "files_written": n,
        "models_dir": os.environ.get("ML_MODELS_DIR", "/app/apps/ml/models"),
    }


# Health checks and separately API-key-protected admin operations must remain
# available even when the global limiter is saturated. Exempt docs only when
# FastAPI actually registered them.
rate_limit_exempt_paths = {"/health"}
if docs_enabled:
    rate_limit_exempt_paths.update({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
for route in app.routes:
    if route.path in rate_limit_exempt_paths or route.path.startswith("/api/admin/"):
        limiter.exempt(route.endpoint)


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
