"""POST /api/ml/predict — on-demand ML re-inference with live spot.

Phase 1 of the Path B backend build. The route reads the most recent
`em_forecasts.feature_vector` snapshot for (symbol, horizon[, earnings_date]),
substitutes the caller's live spot into the two spot-derived features, and
re-runs the LightGBM point + quantile heads.

Wired into `apps/backend/main.py` via `init_router({...})`, same pattern as
`routers.em`.

Caching:
  - Read-through Upstash cache keyed by (symbol, horizon, rounded spot).
  - 30s TTL. Survives backend restarts so a cold start serves cached
    responses for the first 30s of polling on popular tickers.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from models import (
    MLAvailableHorizon,
    MLBatchPredictItemResponse,
    MLBatchPredictRequest,
    MLBatchPredictResponse,
    MLCoverageHorizonRow,
    MLCoverageRequest,
    MLCoverageResponse,
    MLPredictRequest,
    MLPredictResponse,
)
from services import predict_service

logger = logging.getLogger(__name__)
router = APIRouter()

# init_router populates these from main.py during lifespan.
_state: Dict[str, Any] = {}


def init_router(state: Dict[str, Any]) -> None:
    _state.update(state)


def _pool():
    return _state.get("db_pool")


def _redis():
    return _state.get("redis_client")


VALID_HORIZONS = {1, 2, 3, 7, 14, 21}
RESPONSE_TTL_SECONDS = 30


def _cache_key(req: MLPredictRequest) -> str:
    """Stable cache key. Spot is rounded to 1 decimal so trivial flicker
    in the live tick doesn't blow the cache to bits; 0.1 = ~10bp on a $100
    name, well below the noise floor of an EM prediction."""
    spot_bucket = round(req.spot_override, 1) if req.spot_override is not None else "snap"
    earn = req.earnings_date.isoformat() if req.earnings_date else "auto"
    return f"ml:pred:{req.symbol.upper()}:{req.horizon_days}:{spot_bucket}:{earn}"


async def _cached_get(key: str) -> Optional[Dict[str, Any]]:
    redis = _redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception as exc:
        logger.warning("Redis GET failed for %s: %s", key, exc)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def _cached_set(key: str, payload: Dict[str, Any]) -> None:
    redis = _redis()
    if redis is None:
        return
    try:
        await redis.setex(key, RESPONSE_TTL_SECONDS, json.dumps(payload, default=str))
    except Exception as exc:
        logger.warning("Redis SETEX failed for %s: %s", key, exc)


async def _predict_response(req: MLPredictRequest) -> MLPredictResponse:
    if req.horizon_days not in VALID_HORIZONS:
        raise HTTPException(
            status_code=400,
            detail=f"horizon_days must be one of {sorted(VALID_HORIZONS)}",
        )

    pool = _pool()
    if pool is None:
        # The hybrid/postgres backend wasn't initialised — surface that as
        # 503 so callers know to retry, not as a 500.
        raise HTTPException(status_code=503, detail="Postgres pool not available")

    cache_key = _cache_key(req)
    hit = await _cached_get(cache_key)
    if hit is not None:
        hit["source"] = "cached"
        return MLPredictResponse(**hit)

    snapshot = await predict_service.fetch_latest_feature_snapshot(
        pool,
        req.symbol,
        req.horizon_days,
        req.earnings_date,
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No fresh feature snapshot for {req.symbol.upper()} "
                f"horizon={req.horizon_days}d "
                f"(max age {predict_service.MAX_SNAPSHOT_AGE_DAYS}d). "
                "The nightly batch may not have scored this symbol recently."
            ),
        )

    result = predict_service.predict(
        feature_vector=snapshot["feature_vector"],
        snapshot_date=snapshot["snapshot_date"],
        horizon=req.horizon_days,
        spot_override=req.spot_override or snapshot.get("spot_at_snapshot"),
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail=f"No model loaded for horizon T-{req.horizon_days}",
        )

    response = MLPredictResponse(
        symbol=req.symbol.upper(),
        horizon_days=req.horizon_days,
        em_ml_pct=result.em_ml_pct,
        em_ml_abs=result.em_ml_abs,
        quantiles=result.quantiles,
        spot_used=result.spot_used,
        feature_snapshot_date=result.feature_snapshot_date,
        earnings_date=snapshot.get("earnings_date"),
        source="live",
        served_at=datetime.now(timezone.utc),
    )

    # Mirror into Upstash so the next caller (and any backend instance)
    # serves the cached response without re-running the model.
    await _cached_set(cache_key, response.model_dump(mode="json"))
    return response


@router.post("/api/ml/predict", response_model=MLPredictResponse)
async def predict_endpoint(req: MLPredictRequest) -> MLPredictResponse:
    return await _predict_response(req)


@router.post("/api/ml/batch-predict", response_model=MLBatchPredictResponse)
async def batch_predict_endpoint(req: MLBatchPredictRequest) -> MLBatchPredictResponse:
    items = []
    for item in req.items:
        try:
            response = await _predict_response(item)
            items.append(
                MLBatchPredictItemResponse(
                    ok=True,
                    symbol=item.symbol.upper(),
                    horizon_days=item.horizon_days,
                    earnings_date=item.earnings_date,
                    response=response,
                )
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
            items.append(
                MLBatchPredictItemResponse(
                    ok=False,
                    symbol=item.symbol.upper(),
                    horizon_days=item.horizon_days,
                    earnings_date=item.earnings_date,
                    error_status=exc.status_code,
                    error=detail,
                )
            )

    return MLBatchPredictResponse(
        items=items,
        served_at=datetime.now(timezone.utc),
    )


@router.post("/api/ml/coverage", response_model=MLCoverageResponse)
async def coverage_endpoint(req: MLCoverageRequest) -> MLCoverageResponse:
    pool = _pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Postgres pool not available")

    window = req.fresh_window_days
    symbol = req.symbol.upper() if req.symbol else None

    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT
              COUNT(*)::int AS total_feature_rows,
              COUNT(DISTINCT act_symbol) FILTER (
                WHERE snapshot_date >= CURRENT_DATE - ($1 || ' days')::interval
              )::int AS fresh_distinct_symbols,
              COUNT(DISTINCT (act_symbol, earnings_date)) FILTER (
                WHERE snapshot_date >= CURRENT_DATE - ($1 || ' days')::interval
              )::int AS fresh_distinct_events
            FROM em_forecasts
            WHERE feature_vector IS NOT NULL
            """,
            str(window),
        )
        by_horizon = await conn.fetch(
            """
            SELECT
              model_horizon::int AS horizon_days,
              COUNT(*)::int AS total_rows,
              COUNT(*) FILTER (
                WHERE snapshot_date >= CURRENT_DATE - ($1 || ' days')::interval
              )::int AS fresh_rows,
              COUNT(DISTINCT act_symbol) FILTER (
                WHERE snapshot_date >= CURRENT_DATE - ($1 || ' days')::interval
              )::int AS fresh_symbols,
              COUNT(DISTINCT (act_symbol, earnings_date)) FILTER (
                WHERE snapshot_date >= CURRENT_DATE - ($1 || ' days')::interval
              )::int AS fresh_events,
              MIN(snapshot_date) AS earliest_snapshot,
              MAX(snapshot_date) AS latest_snapshot
            FROM em_forecasts
            WHERE feature_vector IS NOT NULL
            GROUP BY model_horizon
            ORDER BY model_horizon
            """,
            str(window),
        )

        available = []
        if symbol is not None:
            params = [symbol]
            where = """
                WHERE act_symbol = $1
                  AND feature_vector IS NOT NULL
            """
            if req.earnings_date is not None:
                params.append(req.earnings_date)
                where += " AND earnings_date = $2"
            rows = await conn.fetch(
                f"""
                SELECT
                  model_horizon::int AS horizon_days,
                  earnings_date,
                  snapshot_date,
                  (CURRENT_DATE - snapshot_date)::int AS snapshot_age_days,
                  spot_price
                FROM em_forecasts
                {where}
                ORDER BY earnings_date DESC, model_horizon
                """,
                *params,
            )
            available = [
                MLAvailableHorizon(
                    horizon_days=row["horizon_days"],
                    earnings_date=row["earnings_date"],
                    snapshot_date=row["snapshot_date"],
                    snapshot_age_days=row["snapshot_age_days"],
                    spot_price=float(row["spot_price"]) if row["spot_price"] is not None else None,
                )
                for row in rows
            ]

    return MLCoverageResponse(
        total_feature_rows=totals["total_feature_rows"] if totals else 0,
        fresh_window_days=window,
        fresh_distinct_symbols=totals["fresh_distinct_symbols"] if totals else 0,
        fresh_distinct_events=totals["fresh_distinct_events"] if totals else 0,
        rows_by_horizon=[
            MLCoverageHorizonRow(
                horizon_days=row["horizon_days"],
                total_rows=row["total_rows"],
                fresh_rows=row["fresh_rows"],
                fresh_symbols=row["fresh_symbols"],
                fresh_events=row["fresh_events"],
                earliest_snapshot=row["earliest_snapshot"],
                latest_snapshot=row["latest_snapshot"],
            )
            for row in by_horizon
        ],
        symbol=symbol,
        earnings_date=req.earnings_date,
        available_horizons=available,
        checked_at=datetime.now(timezone.utc),
    )
