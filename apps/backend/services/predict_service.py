"""On-demand re-inference with a live spot price.

The nightly batch (`scripts/daily_score.py`) writes the exact feature vector
it scored with into `em_forecasts.feature_vector` (JSONB). This service:

1. Queries that vector for a given (symbol, earnings_date, horizon).
2. Substitutes in the caller's live spot, recomputing the two spot-derived
   features (`underlying_price`, `log_price`).
3. Runs `model.predict` against the substituted vector.
4. Returns point + quantile predictions and the freshness metadata the
   route layer needs to label the response.

Why not reuse `MLService`/`MLServingPipeline`: those expect a populated
DuckDB chain-data view at predict time. We deliberately avoid the chain
parquet on Railway — the persisted `feature_vector` is the snapshot.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Where the .joblib model files live inside the container. The Dockerfile
# bakes `apps/ml/models/` into the image at this path, so cold starts don't
# pay an R2 fetch. Override via env if you ever move the models to the
# mounted volume.
DEFAULT_MODELS_DIR = "/app/apps/ml/models"


def _models_dir() -> Path:
    return Path(os.getenv("ML_MODELS_DIR", DEFAULT_MODELS_DIR))


# Quantile suffixes we expect alongside the point model file.
_QUANTILES = [10, 25, 50, 75, 90]


@dataclass
class _ModelBundle:
    """One horizon's loaded artifacts. Created lazily on first request."""
    estimator: Any                          # fitted LightGBM regressor
    calibrator: Any                          # optional isotonic calibrator
    feature_names: List[str]                 # exact column order the model wants
    quantile_estimators: Dict[int, Any]      # {10: model, 25: model, ...}


_BUNDLE_CACHE: Dict[int, _ModelBundle] = {}
_BUNDLE_LOCK = Lock()


def _unwrap(payload: Any) -> tuple[Any, Any, List[str]]:
    """Pull (estimator, calibrator, feature_names) out of whatever joblib loads."""
    if isinstance(payload, dict):
        estimator = payload.get("model")
        calibrator = payload.get("calibrator")
        feature_names = list(payload.get("feature_names") or [])
        return estimator, calibrator, feature_names
    estimator = payload
    feature_names = list(getattr(estimator, "feature_name_", []) or [])
    return estimator, None, feature_names


def get_bundle(horizon: int) -> Optional[_ModelBundle]:
    """Lazy-load and cache the point + quantile estimators for `horizon`.

    Returns None if the horizon has no model file on disk — the route layer
    surfaces that as a 404 rather than a 500.
    """
    cached = _BUNDLE_CACHE.get(horizon)
    if cached is not None:
        return cached

    with _BUNDLE_LOCK:
        cached = _BUNDLE_CACHE.get(horizon)
        if cached is not None:
            return cached

        models_dir = _models_dir()
        point_path = models_dir / f"lgbm_T{horizon}.joblib"
        if not point_path.exists():
            logger.warning("No model file for horizon %s at %s", horizon, point_path)
            return None

        estimator, calibrator, feature_names = _unwrap(joblib.load(point_path))
        if estimator is None or not feature_names:
            logger.error("T-%s model is unusable (no estimator/feature_names)", horizon)
            return None

        quantile_estimators: Dict[int, Any] = {}
        for q in _QUANTILES:
            q_path = models_dir / f"lgbm_T{horizon}_q{q:02d}.joblib"
            if not q_path.exists():
                continue
            q_est, _, _ = _unwrap(joblib.load(q_path))
            if q_est is not None:
                quantile_estimators[q] = q_est

        bundle = _ModelBundle(
            estimator=estimator,
            calibrator=calibrator,
            feature_names=feature_names,
            quantile_estimators=quantile_estimators,
        )
        _BUNDLE_CACHE[horizon] = bundle
        logger.info(
            "Loaded T-%s bundle (%d features, %d quantile heads)",
            horizon, len(feature_names), len(quantile_estimators),
        )
        return bundle


def reset_cache() -> None:
    """For tests. Drops the loaded-model cache."""
    with _BUNDLE_LOCK:
        _BUNDLE_CACHE.clear()


# ─── Feature substitution ────────────────────────────────────────────────


def _substitute_spot(
    feature_vector: Dict[str, float],
    live_spot: float,
) -> Dict[str, float]:
    """Return a copy of feature_vector with the two spot-derived features
    swapped in. Other 18 features stay at their snapshot values.

    The model was trained with `underlying_price = spot at snapshot` and
    `log_price = log(spot at snapshot)`, so we have to keep those two in
    lock-step. Everything else (IV, Greeks, term slope, surprise history)
    is anchored to the nightly chain and not affected by an intraday move.
    """
    if live_spot is None or not (live_spot > 0):
        # Defensive: a non-positive spot would NaN out log_price and the
        # predict call would emit NaN. Skip the substitution and serve the
        # snapshot's own value.
        return dict(feature_vector)
    out = dict(feature_vector)
    out["underlying_price"] = float(live_spot)
    out["log_price"] = float(math.log(max(live_spot, 1.0)))
    return out


def _build_X(
    feature_vector: Dict[str, float],
    feature_names: List[str],
) -> pd.DataFrame:
    """Project the dict onto the model's expected feature order. Missing
    keys (older snapshots, schema drift) become NaN — LightGBM tolerates
    NaN as "missing" by default."""
    row = {name: feature_vector.get(name, np.nan) for name in feature_names}
    return pd.DataFrame([row], columns=feature_names)


# ─── Predict ─────────────────────────────────────────────────────────────


@dataclass
class PredictionResult:
    em_ml_pct: float
    em_ml_abs: float
    quantiles: Dict[int, float]   # {10: 0.034, 25: ..., 90: 0.083} (pct, abs(move))
    feature_snapshot_date: str    # YYYY-MM-DD ISO
    spot_used: float
    horizon: int


def predict(
    feature_vector: Dict[str, float],
    snapshot_date: date,
    horizon: int,
    spot_override: Optional[float],
) -> Optional[PredictionResult]:
    """Run the point + quantile heads against `feature_vector` with the
    caller's spot. Returns None if no model is available for `horizon`.
    """
    bundle = get_bundle(horizon)
    if bundle is None:
        return None

    # Pick the spot we'll report and substitute. None → use whatever
    # snapshot value lives in the feature vector (no-op substitution).
    spot_used = spot_override
    if spot_used is None:
        spot_used = float(feature_vector.get("underlying_price", 0.0)) or None

    substituted = _substitute_spot(feature_vector, spot_used) if spot_used else dict(feature_vector)
    X = _build_X(substituted, bundle.feature_names)

    em_pct = float(bundle.estimator.predict(X)[0])
    if bundle.calibrator is not None:
        try:
            em_pct = float(bundle.calibrator.predict([em_pct])[0])
        except Exception:
            # Calibrator may be sklearn version-skew incompatible. Predict still
            # works — log once per process and continue uncalibrated.
            logger.warning("Calibrator predict failed for T-%s; serving raw", horizon)

    quantiles: Dict[int, float] = {}
    for q, q_est in bundle.quantile_estimators.items():
        try:
            quantiles[q] = float(q_est.predict(X)[0])
        except Exception as exc:
            logger.warning("Quantile %s predict failed: %s", q, exc)

    em_abs = em_pct * spot_used if spot_used else 0.0
    return PredictionResult(
        em_ml_pct=em_pct,
        em_ml_abs=em_abs,
        quantiles=quantiles,
        feature_snapshot_date=snapshot_date.isoformat(),
        spot_used=float(spot_used or 0.0),
        horizon=horizon,
    )


# ─── Postgres fetch ──────────────────────────────────────────────────────


# How stale can a feature snapshot be before we refuse to re-infer against
# it. Chain data ages quickly — past a week, the IV / Greeks / skew are
# stale enough that "re-inference with live spot" is misleading.
MAX_SNAPSHOT_AGE_DAYS = 7


async def fetch_latest_feature_snapshot(
    pool: Any,                # asyncpg.Pool, kept Any-typed to avoid hard import
    symbol: str,
    horizon: int,
    earnings_date: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """Return the most recent `em_forecasts` row that has a non-NULL
    feature_vector for (symbol, horizon[, earnings_date]). None if there
    isn't one within MAX_SNAPSHOT_AGE_DAYS.

    Shape: {snapshot_date, earnings_date, feature_vector: dict, spot_at_snapshot}.
    """
    base = """
        SELECT snapshot_date, earnings_date, feature_vector, spot_price
        FROM em_forecasts
        WHERE act_symbol = $1
          AND model_horizon = $2
          AND feature_vector IS NOT NULL
          AND snapshot_date >= CURRENT_DATE - ($3 || ' days')::interval
    """
    params: List[Any] = [symbol.upper(), horizon, str(MAX_SNAPSHOT_AGE_DAYS)]
    if earnings_date is not None:
        base += " AND earnings_date = $4"
        params.append(earnings_date)
    base += " ORDER BY snapshot_date DESC LIMIT 1"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(base, *params)
    if row is None:
        return None
    feature_vector = row["feature_vector"]
    # asyncpg returns JSONB as a Python dict already if the codec is set;
    # some Neon configurations hand back a JSON string. Normalize.
    if isinstance(feature_vector, str):
        import json as _json
        try:
            feature_vector = _json.loads(feature_vector)
        except (ValueError, TypeError):
            return None
    if not isinstance(feature_vector, dict):
        return None
    return {
        "snapshot_date": row["snapshot_date"],
        "earnings_date": row["earnings_date"],
        "feature_vector": feature_vector,
        "spot_at_snapshot": float(row["spot_price"]) if row["spot_price"] is not None else None,
    }


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
