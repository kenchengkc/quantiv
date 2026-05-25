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

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import joblib
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
    loaded_at: datetime
    model_version: Optional[str]
    model_trained_at: Optional[datetime]
    feature_schema_hash: str
    val_mae: Optional[float]


_BUNDLE_CACHE: Dict[int, _ModelBundle] = {}
_BUNDLE_LOCK = Lock()


def _feature_schema_hash(feature_names: List[str]) -> str:
    """Stable fingerprint for the exact model feature order."""
    payload = "\n".join(feature_names)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _metadata_for_horizon(models_dir: Path, horizon: int) -> Dict[str, Any]:
    meta_path = models_dir / f"metadata_T{horizon}.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except (OSError, ValueError, TypeError):
        logger.warning("Failed to read model metadata at %s", meta_path)
        return {}


def _mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


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
        metadata = _metadata_for_horizon(models_dir, horizon)

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
            loaded_at=datetime.now(timezone.utc),
            model_version=metadata.get("version"),
            model_trained_at=_parse_datetime(metadata.get("trained_at")),
            feature_schema_hash=_feature_schema_hash(feature_names),
            val_mae=float(metadata["val_mae"]) if metadata.get("val_mae") is not None else None,
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


def loaded_horizons() -> List[int]:
    return sorted(_BUNDLE_CACHE)


def model_inventory() -> List[Dict[str, Any]]:
    """Return file/metadata status for all model horizons without forcing
    LightGBM joblib loads. Used by `/api/ml/status` so health checks stay
    cheap and don't mutate the model cache.
    """
    models_dir = _models_dir()
    horizons = set()
    for path in models_dir.glob("lgbm_T*.joblib"):
        suffix = path.stem.replace("lgbm_T", "")
        if suffix.isdigit():
            horizons.add(int(suffix))
    for path in models_dir.glob("metadata_T*.json"):
        suffix = path.stem.replace("metadata_T", "")
        if suffix.isdigit():
            horizons.add(int(suffix))

    rows: List[Dict[str, Any]] = []
    for horizon in sorted(horizons):
        point_path = models_dir / f"lgbm_T{horizon}.joblib"
        meta_path = models_dir / f"metadata_T{horizon}.json"
        metadata = _metadata_for_horizon(models_dir, horizon)
        feature_names = list(metadata.get("feature_cols") or [])
        cached = _BUNDLE_CACHE.get(horizon)
        if cached is not None:
            feature_names = cached.feature_names
        quantile_count = sum(
            1 for q in _QUANTILES if (models_dir / f"lgbm_T{horizon}_q{q:02d}.joblib").exists()
        )
        rows.append({
            "horizon_days": horizon,
            "point_model_exists": point_path.exists(),
            "quantile_model_count": quantile_count,
            "feature_count": len(feature_names) if feature_names else None,
            "feature_schema_hash": (
                cached.feature_schema_hash
                if cached is not None
                else (_feature_schema_hash(feature_names) if feature_names else None)
            ),
            "model_version": (
                cached.model_version if cached is not None else metadata.get("version")
            ),
            "trained_at": (
                cached.model_trained_at
                if cached is not None
                else _parse_datetime(metadata.get("trained_at"))
            ),
            "val_mae": (
                cached.val_mae
                if cached is not None
                else (float(metadata["val_mae"]) if metadata.get("val_mae") is not None else None)
            ),
            "loaded": cached is not None,
            "loaded_at": cached.loaded_at if cached is not None else None,
            "model_mtime": _mtime(point_path),
            "metadata_mtime": _mtime(meta_path),
        })
    return rows


# ─── Feature substitution ────────────────────────────────────────────────


def _substitute_spot(
    feature_vector: Dict[str, float],
    live_spot: float,
) -> Dict[str, float]:
    """Return a copy of feature_vector with the spot-derived features
    swapped in. Schema-flexible — the v3 production models use `log_spot`,
    the older MVP1 models used `underlying_price` + `log_price`. We update
    whichever keys exist in the vector so a stray schema swap (R2 retrain
    landing different names) doesn't silently no-op the override.

    Everything else (IV, Greeks, term slope, surprise history) is anchored
    to the nightly chain and unaffected by an intraday move.
    """
    if live_spot is None or not (live_spot > 0):
        # Defensive: a non-positive spot would NaN out log values and the
        # predict call would emit NaN. Skip the substitution and serve the
        # snapshot's own value.
        return dict(feature_vector)
    out = dict(feature_vector)
    log_spot = float(math.log(max(live_spot, 1.0)))
    # MVP1 schema names
    if "underlying_price" in out:
        out["underlying_price"] = float(live_spot)
    if "log_price" in out:
        out["log_price"] = log_spot
    # v3 schema name
    if "log_spot" in out:
        out["log_spot"] = log_spot
    return out


def _build_X(
    feature_vector: Dict[str, float],
    feature_names: List[str],
) -> pd.DataFrame:
    """Project the dict onto the model's expected feature order. Missing
    keys (older snapshots, schema drift) and Python None values both
    become NaN — LightGBM tolerates NaN as "missing" but rejects pandas
    `object` dtypes (which is what a column with `None` defaults to).
    `pd.to_numeric(..., errors='coerce')` forces float and substitutes
    NaN for anything non-numeric.
    """
    row = {name: feature_vector.get(name) for name in feature_names}
    df = pd.DataFrame([row], columns=feature_names)
    return df.apply(pd.to_numeric, errors="coerce")


# ─── Predict ─────────────────────────────────────────────────────────────


@dataclass
class PredictionResult:
    em_ml_pct: float
    em_ml_abs: float
    quantiles: Dict[int, float]   # {10: 0.034, 25: ..., 90: 0.083} (pct, abs(move))
    feature_snapshot_date: str    # YYYY-MM-DD ISO
    spot_used: float
    horizon: int
    model_version: Optional[str]
    model_trained_at: Optional[datetime]
    model_loaded_at: datetime
    feature_schema_hash: str


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
    # Calibration is intentionally skipped to match scripts/daily_score.py,
    # which writes raw model output to em_forecasts.em_ml_pct. Applying it
    # here would cause the live /api/ml/predict number to drift away from
    # the static-JSON value shown in the calendar / ticker page. The bundled
    # IsotonicRegression calibrators were also pickled under sklearn 1.4.2;
    # the runtime is 1.8+, so even when the call succeeds it triggers
    # InconsistentVersionWarning and may produce subtly wrong outputs. Once
    # the v3 models get retrained on the current sklearn, we can turn
    # calibration on in BOTH the nightly batch and this route.
    _ = bundle.calibrator  # keep loaded so we can ship it again later

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
        model_version=bundle.model_version,
        model_trained_at=bundle.model_trained_at,
        model_loaded_at=bundle.loaded_at,
        feature_schema_hash=bundle.feature_schema_hash,
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
        SELECT
          snapshot_date,
          earnings_date,
          feature_vector,
          spot_price,
          scored_at,
          (CURRENT_DATE - snapshot_date)::int AS snapshot_age_days
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
        "forecast_scored_at": row["scored_at"],
        "snapshot_age_days": row["snapshot_age_days"],
    }


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
