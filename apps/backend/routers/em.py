"""Expected-move and ML forecast endpoints."""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from typing import Optional, List, Dict, Any
from datetime import datetime, date
import json
import structlog

from models import (
    ExpectedMoveRequest, ExpectedMoveResponse,
    EmForecastLatestResponse, EmHistoryResponse, EmExpiriesResponse,
    HealthResponse,
)

logger = structlog.get_logger()

router = APIRouter()


def _parse_window_to_days(window: str) -> int:
    """Parse window strings like '90d' into integer days; default to 90 if invalid."""
    try:
        w = window.strip().lower()
        if w.endswith('d'):
            return max(1, int(w[:-1]))
        return max(1, int(w))
    except Exception:
        return 90


# ---------------------------------------------------------------------------
# The router needs access to shared singletons managed by main.py.  We use a
# lightweight "state" dict that main.py populates during lifespan startup.
# ---------------------------------------------------------------------------
_state: Dict[str, Any] = {}


def init_router(state: Dict[str, Any]):
    """Called once by main.py after services are initialised."""
    _state.update(state)


def _backend():
    return _state["data_backend"]

def _redis():
    return _state["redis_client"]

def _http():
    return _state["http_client"]

def _ml():
    return _state.get("ml_service")

def _backend_mode():
    return _state.get("DATA_BACKEND_MODE", "postgres")


# ---- health ---------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    import os, duckdb as _ddb
    services: Dict[str, str] = {}
    backend = _backend_mode()
    if backend in ("postgres", "hybrid"):
        try:
            pool = _state.get("db_pool")
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            services["postgres"] = "healthy"
        except Exception:
            services["postgres"] = "unhealthy"
    if backend in ("duckdb", "hybrid"):
        try:
            dc = _state.get("duckdb_conn")
            _ = dc.execute("SELECT 1").fetchone()
            services["duckdb"] = "healthy"
        except Exception:
            services["duckdb"] = "unhealthy"
    try:
        await _redis().ping()
        services["redis"] = "healthy"
    except Exception:
        services["redis"] = "unhealthy"
    services["polygon"] = "configured" if os.getenv("POLYGON_API_KEY") else "not_configured"
    status = "healthy" if all(s in ["healthy", "configured"] for s in services.values()) else "degraded"
    return HealthResponse(status=status, timestamp=datetime.now(), services=services)


# ---- expected-move ---------------------------------------------------------

@router.post("/api/expected-move", response_model=ExpectedMoveResponse)
async def get_expected_move(request: ExpectedMoveRequest):
    """Get expected move forecasts for a symbol"""
    import os
    symbol = request.symbol.upper()
    logger.info("Expected move request", symbol=symbol, horizons=request.horizons)

    # Cache
    cache_key = f"em_forecast:{symbol}:{':'.join(sorted(request.horizons))}"
    try:
        cached = await _redis().get(cache_key)
        if cached:
            data = json.loads(cached)
            cached_time = datetime.fromisoformat(data["timestamp"])
            from datetime import timedelta
            if datetime.now() - cached_time < timedelta(minutes=5):
                logger.info("Returning cached forecast", symbol=symbol)
                return ExpectedMoveResponse(**data)
    except Exception as e:
        logger.warning("Cache read failed", error=str(e))

    forecasts = await _backend().get_latest_forecasts(symbol, request.horizons)
    if not forecasts:
        raise HTTPException(status_code=404, detail=f"No forecasts found for {symbol}")

    live_data = None
    if request.include_live and os.getenv("POLYGON_API_KEY"):
        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev"
            response = await _http().get(url)
            if response.status_code == 200:
                rd = response.json()
                if rd.get("results"):
                    r = rd["results"][0]
                    live_data = {
                        "symbol": symbol,
                        "price": r.get("c"),
                        "change": r.get("c", 0) - r.get("o", 0),
                        "change_percent": ((r.get("c", 0) - r.get("o", 0)) / r.get("o", 1)) * 100,
                        "volume": r.get("v"),
                        "timestamp": datetime.now(),
                    }
        except Exception as e:
            logger.warning("Live data fetch failed", symbol=symbol, error=str(e))

    response_data = {
        "symbol": symbol,
        "timestamp": datetime.now(),
        "forecasts": forecasts,
        "live_data": live_data,
        "metadata": {"forecast_count": len(forecasts), "horizons_requested": request.horizons, "has_live_data": live_data is not None},
    }
    try:
        await _redis().setex(cache_key, 300, json.dumps(response_data, default=str))
    except Exception as e:
        logger.warning("Cache write failed", error=str(e))
    return ExpectedMoveResponse(**response_data)


# ---- EM forecast / history / expiries --------------------------------------

@router.get("/em/forecast", response_model=EmForecastLatestResponse)
async def em_forecast(symbol: str, exp: str):
    """Latest baseline EM record for (symbol, exp)."""
    sym = symbol.upper()
    try:
        exp_date = date.fromisoformat(exp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid exp date; use YYYY-MM-DD")

    cache_key = f"em:forecast:{sym}:{exp_date.isoformat()}"
    try:
        cached = await _redis().get(cache_key)
        if cached:
            return EmForecastLatestResponse(**json.loads(cached))
    except Exception as e:
        logger.warning("EM forecast cache read failed", error=str(e))

    rec = await _backend().get_latest_for_symbol_exp(sym, exp_date)
    if not rec:
        raise HTTPException(status_code=404, detail="No forecast found")

    payload = {
        "symbol": sym, "exp": exp_date, "quote_ts": rec["quote_ts"],
        "horizon": rec["horizon"], "em_baseline": rec.get("em_baseline"),
        "band68_low": rec.get("band68_low"), "band68_high": rec.get("band68_high"),
        "band95_low": rec.get("band95_low"), "band95_high": rec.get("band95_high"),
        "metadata": {"source": "em_forecasts", "cache": False},
    }
    try:
        await _redis().setex(cache_key, 600, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("EM forecast cache write failed", error=str(e))
    return EmForecastLatestResponse(**payload)


@router.get("/em/history", response_model=EmHistoryResponse)
async def em_history(symbol: str, exp: str, window: str = "90d"):
    """Timeseries for baseline EM for charting."""
    sym = symbol.upper()
    try:
        exp_date = date.fromisoformat(exp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid exp date; use YYYY-MM-DD")
    days = _parse_window_to_days(window)

    cache_key = f"em:history:{sym}:{exp_date.isoformat()}:{days}d"
    try:
        cached = await _redis().get(cache_key)
        if cached:
            return EmHistoryResponse(**json.loads(cached))
    except Exception as e:
        logger.warning("EM history cache read failed", error=str(e))

    rows = await _backend().get_history_for_symbol_exp(sym, exp_date, days)
    items = [
        {"quote_ts": r["quote_ts"], "em_baseline": r.get("em_baseline"),
         "band68_low": r.get("band68_low"), "band68_high": r.get("band68_high"),
         "band95_low": r.get("band95_low"), "band95_high": r.get("band95_high")}
        for r in rows
    ]
    payload = {"symbol": sym, "exp": exp_date, "window": f"{days}d", "items": items,
               "metadata": {"count": len(items), "source": "em_forecasts", "cache": False}}
    try:
        await _redis().setex(cache_key, 600, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("EM history cache write failed", error=str(e))
    return EmHistoryResponse(**payload)


@router.get("/em/expiries", response_model=EmExpiriesResponse)
async def em_expiries(symbol: str, window: str = "120d"):
    """List upcoming expiries with forecasts."""
    sym = symbol.upper()
    days = _parse_window_to_days(window)

    cache_key = f"em:expiries:{sym}:{days}d"
    try:
        cached = await _redis().get(cache_key)
        if cached:
            return EmExpiriesResponse(**json.loads(cached))
    except Exception as e:
        logger.warning("EM expiries cache read failed", error=str(e))

    expiries = await _backend().get_expiries(sym, days)
    payload = {"symbol": sym, "expiries": expiries,
               "metadata": {"count": len(expiries), "source": "em_forecasts", "cache": False}}
    try:
        await _redis().setex(cache_key, 600, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("EM expiries cache write failed", error=str(e))
    return EmExpiriesResponse(**payload)


# ---- ML endpoints ----------------------------------------------------------

@router.get("/em/ml-forecast")
async def em_ml_forecast(symbol: str, earnings_date: str, sector: Optional[str] = None):
    """ML-enhanced expected move forecast."""
    sym = symbol.upper()
    ml = _ml()
    if not ml:
        raise HTTPException(status_code=503, detail="ML service not available")

    cache_key = f"em:ml:{sym}:{earnings_date}:{sector or 'none'}"
    try:
        cached = await _redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("ML forecast cache read failed", error=str(e))

    try:
        forecast = ml.predict_expected_move(symbol=sym, earnings_date=earnings_date, sector=sector)
        if not forecast:
            raise HTTPException(status_code=404, detail=f"No ML forecast available for {sym}")
        try:
            await _redis().setex(cache_key, 300, json.dumps(forecast, default=str))
        except Exception as e:
            logger.warning("ML forecast cache write failed", error=str(e))
        return forecast
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ML forecast generation failed", symbol=sym, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to generate ML forecast")


@router.get("/em/ml-info")
async def em_ml_info():
    """ML pipeline status."""
    ml = _ml()
    if not ml:
        return {"status": "unavailable", "message": "ML service not initialized"}
    return ml.get_model_info()


@router.get("/api/symbols")
async def get_available_symbols():
    """Symbols with available forecasts."""
    return await _backend().get_symbols(7)


@router.get("/api/symbols/{symbol}/history")
async def get_symbol_history(symbol: str, days: int = 30):
    """Historical forecasts for a symbol."""
    return await _backend().get_symbol_history_all_horizons(symbol.upper(), days)


@router.get("/api/ml/predict/{symbol}")
async def get_ml_prediction(symbol: str):
    ml = _ml()
    if not ml:
        raise HTTPException(status_code=503, detail="ML service not available")
    prediction = ml.predict_expected_move(symbol.upper())
    if not prediction:
        raise HTTPException(status_code=404, detail=f"No ML prediction available for {symbol}")
    return prediction


@router.get("/api/ml/info")
async def get_ml_info():
    ml = _ml()
    if not ml:
        return {"status": "unavailable", "message": "ML service not initialized"}
    return ml.get_model_info()


@router.get("/api/ml/symbols")
async def get_ml_symbols():
    ml = _ml()
    if not ml:
        raise HTTPException(status_code=503, detail="ML service not available")
    return ml.get_available_symbols()


@router.get("/api/ml/forecasts")
async def get_all_ml_forecasts(days_ahead: int = 30):
    ml = _ml()
    if not ml:
        raise HTTPException(status_code=503, detail="ML service not available")
    forecasts = ml.get_all_upcoming_forecasts(days_ahead)
    return {"forecasts": forecasts, "count": len(forecasts), "days_ahead": days_ahead,
            "generated_at": datetime.now().isoformat()}

