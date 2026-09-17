"""Validation for live forecast artifacts that may contain ML-only rows.

The original ``pipeline_validation.validate_forecast_artifact`` is intentionally
strict about option evidence: every row must carry a decision-eligible straddle.
That remains the correct contract for rows that publish strict option evidence.

Live ML scoring can now produce a forecast when strict option features are
missing, because LightGBM natively handles missing feature values. This module
composes the strict validator instead of weakening it:

* rows carrying *any* strict option evidence are passed through the existing
  strict forecast validator unchanged;
* rows carrying no strict option evidence at all are validated against the ML
  serving contract (model handoff, feature-vector schema, finite prediction,
  quantile ordering, point-in-time dates, and freshness).

A partially populated option row is never treated as ML-only; it goes to the
strict validator and fails closed if the evidence is incomplete.
"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.pipeline_validation import (
    FORECAST_REQUIRED_COLUMNS,
    PipelineValidationError,
    ValidationIssue,
    validate_forecast_artifact,
)


STRICT_OPTION_EVIDENCE_COLUMNS = (
    "atm_iv",
    "atm_strike",
    "call_strike",
    "put_strike",
    "call_bid",
    "call_ask",
    "call_mid",
    "call_relative_spread",
    "call_quote_timestamp",
    "put_bid",
    "put_ask",
    "put_mid",
    "put_relative_spread",
    "put_quote_timestamp",
    "straddle_bid",
    "straddle_ask",
    "straddle_mid",
    "straddle_relative_spread",
    "quote_timestamp_precision",
    "market_data_mode",
    "quote_quality_status",
    "liquidity_tier",
    "liquidity_tier_method",
    "quote_rejection_reason",
    "em_math_pct",
    "correction_factor",
)

ML_CORE_NUMERIC_COLUMNS = (
    "spot_price",
    "em_ml_pct",
    "em_ml_abs",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
)


def _issue(
    issues: list[ValidationIssue],
    artifact: Path,
    code: str,
    message: str,
) -> None:
    issues.append(ValidationIssue("forecasts", str(artifact), code, message))


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def _strict_feature_vector(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError("feature_vector must be a JSON string")

    def reject_constant(token: str) -> None:
        raise ValueError(f"non-standard JSON constant {token}")

    payload = json.loads(value, parse_constant=reject_constant)
    if not isinstance(payload, dict):
        raise ValueError("feature_vector must decode to an object")
    return payload


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    )


def _option_evidence_mask(frame: pd.DataFrame) -> pd.Series:
    # Required-column validation runs first, so all of these columns exist.
    return frame[list(STRICT_OPTION_EVIDENCE_COLUMNS)].notna().any(axis=1)


def _validate_ml_only_rows(
    frame: pd.DataFrame,
    *,
    artifact: Path,
    models_dir: Path,
    max_age_days: int,
    now: datetime | None,
    max_point_median_gap: float,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    issues: list[ValidationIssue] = []
    if frame.empty:
        return issues, {"optionless_ml_rows": 0}

    # A live ML-only row must be entirely optionless at the strict evidence
    # boundary. This is deliberately stronger than merely checking atm_iv.
    partial_option_rows = int(_option_evidence_mask(frame).sum())
    if partial_option_rows:
        _issue(
            issues,
            artifact,
            "partial_option_evidence_on_ml_only_row",
            f"found {partial_option_rows} ML-only rows carrying strict option evidence",
        )

    numeric = frame[list(ML_CORE_NUMERIC_COLUMNS)].apply(
        pd.to_numeric, errors="coerce"
    )
    non_finite_core = int((~np.isfinite(numeric.to_numpy(dtype=float))).any(axis=1).sum())
    if non_finite_core:
        _issue(
            issues,
            artifact,
            "non_finite_ml_forecast_values",
            f"found {non_finite_core} ML-only rows with non-finite core predictions",
        )

    invalid_spot = int((numeric["spot_price"] <= 0).sum())
    if invalid_spot:
        _issue(
            issues,
            artifact,
            "invalid_ml_only_spot",
            f"found {invalid_spot} ML-only rows without a positive spot price",
        )

    move_cols = ["em_ml_pct", "p10", "p25", "p50", "p75", "p90"]
    out_of_range = int(
        ((numeric[move_cols] < 0) | (numeric[move_cols] > 3)).any(axis=1).sum()
    )
    if out_of_range:
        _issue(
            issues,
            artifact,
            "out_of_range_ml_only_forecasts",
            f"found {out_of_range} ML-only rows outside the 0%–300% move range",
        )

    quantiles = numeric[["p10", "p25", "p50", "p75", "p90"]].to_numpy(
        dtype=float
    )
    crossings = int(np.any(np.diff(quantiles, axis=1) < 0, axis=1).sum())
    if crossings:
        _issue(
            issues,
            artifact,
            "crossed_served_quantiles",
            f"found {crossings} ML-only rows with non-monotone quantiles",
        )

    outside_band = int(
        (
            (numeric["em_ml_pct"] < numeric["p10"])
            | (numeric["em_ml_pct"] > numeric["p90"])
        ).sum()
    )
    if outside_band:
        _issue(
            issues,
            artifact,
            "point_outside_forecast_band",
            f"found {outside_band} ML-only point estimates outside P10–P90",
        )

    median_gap = float((numeric["em_ml_pct"] - numeric["p50"]).abs().max())
    if math.isfinite(median_gap) and median_gap > max_point_median_gap:
        _issue(
            issues,
            artifact,
            "point_median_divergence",
            (
                f"maximum ML-only point/P50 gap {median_gap:.4f} exceeds "
                f"{max_point_median_gap:.4f}"
            ),
        )

    expected_abs = numeric["em_ml_pct"] * numeric["spot_price"]
    absolute_mismatches = int(
        (~np.isclose(numeric["em_ml_abs"], expected_abs, rtol=1e-6, atol=1e-8)).sum()
    )
    if absolute_mismatches:
        _issue(
            issues,
            artifact,
            "absolute_forecast_mismatch",
            (
                f"found {absolute_mismatches} ML-only rows where ML $ move does "
                "not match pct × spot"
            ),
        )

    earnings_dates = pd.to_datetime(frame["earnings_date"], errors="coerce")
    snapshot_dates = pd.to_datetime(frame["snapshot_date"], errors="coerce")
    if earnings_dates.isna().any() or snapshot_dates.isna().any():
        _issue(
            issues,
            artifact,
            "invalid_forecast_dates",
            "earnings_date and snapshot_date must be valid for every ML-only row",
        )
    elif (snapshot_dates > earnings_dates).any():
        _issue(
            issues,
            artifact,
            "post_event_feature_snapshot",
            "ML-only feature snapshots must not occur after the earnings event",
        )

    scored_at = pd.to_datetime(frame["scored_at"], errors="coerce", utc=True)
    if scored_at.isna().any():
        _issue(
            issues,
            artifact,
            "invalid_scored_at",
            "scored_at must be parseable for every ML-only row",
        )
    else:
        reference = now or datetime.now(timezone.utc)
        reference = (
            reference if reference.tzinfo else reference.replace(tzinfo=timezone.utc)
        )
        age_days = (
            reference - scored_at.max().to_pydatetime()
        ).total_seconds() / 86_400
        if age_days > max_age_days or age_days < -1:
            _issue(
                issues,
                artifact,
                "stale_forecast_artifact",
                f"newest ML-only score is {age_days:.1f} days old; maximum is {max_age_days}",
            )

    metadata_by_horizon: dict[int, dict[str, Any]] = {}
    horizon_values = pd.to_numeric(frame["model_horizon"], errors="coerce")
    invalid_horizons = int(
        (
            horizon_values.isna()
            | (horizon_values <= 0)
            | (horizon_values % 1 != 0)
        ).sum()
    )
    if invalid_horizons:
        _issue(
            issues,
            artifact,
            "invalid_model_horizons",
            f"found {invalid_horizons} invalid ML-only model horizons",
        )

    valid_horizons = horizon_values[
        horizon_values.notna() & (horizon_values > 0) & (horizon_values % 1 == 0)
    ].astype(int)
    for horizon in sorted(set(valid_horizons)):
        metadata_path = models_dir / f"metadata_T{horizon}.json"
        try:
            metadata = _load_json_object(metadata_path)
            if not list(metadata.get("feature_cols") or []):
                raise ValueError("model metadata has no feature_cols schema")
            metadata_by_horizon[horizon] = metadata
        except Exception as exc:
            _issue(
                issues,
                artifact,
                "missing_forecast_model_metadata",
                f"T-{horizon}: {exc}",
            )

    invalid_vectors = 0
    schema_mismatches = 0
    invalid_feature_values = 0
    for _, row in frame.iterrows():
        try:
            vector = _strict_feature_vector(row["feature_vector"])
            horizon = int(row["model_horizon"])
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
            invalid_vectors += 1
            continue
        metadata = metadata_by_horizon.get(horizon)
        expected_features = list(metadata.get("feature_cols") or []) if metadata else []
        if expected_features and not set(expected_features).issubset(vector):
            schema_mismatches += 1
        if any(
            value is not None and not _finite_number(value) for value in vector.values()
        ):
            invalid_feature_values += 1

    if invalid_vectors:
        _issue(
            issues,
            artifact,
            "invalid_feature_vectors",
            f"found {invalid_vectors} invalid strict-JSON ML-only feature vectors",
        )
    if schema_mismatches:
        _issue(
            issues,
            artifact,
            "feature_vector_schema_mismatch",
            f"found {schema_mismatches} ML-only vectors missing model metadata features",
        )
    if invalid_feature_values:
        _issue(
            issues,
            artifact,
            "invalid_feature_values",
            f"found {invalid_feature_values} ML-only vectors with non-numeric feature values",
        )

    return issues, {
        "optionless_ml_rows": len(frame),
        "non_finite_ml_rows": non_finite_core,
        "invalid_ml_only_spot_rows": invalid_spot,
        "ml_only_quantile_crossings": crossings,
        "ml_only_point_outside_band": outside_band,
        "ml_only_absolute_move_mismatches": absolute_mismatches,
        "ml_only_invalid_feature_vectors": invalid_vectors,
        "ml_only_feature_schema_mismatches": schema_mismatches,
        "ml_only_invalid_feature_values": invalid_feature_values,
    }


def validate_live_forecast_artifact(
    forecast_path: Path,
    *,
    models_dir: Path,
    min_rows: int = 1,
    max_age_days: int = 2,
    now: datetime | None = None,
    max_point_median_gap: float = 0.05,
) -> dict[str, Any]:
    """Validate a live forecast snapshot with strict or optionless ML rows."""

    if not forecast_path.exists():
        return validate_forecast_artifact(
            forecast_path,
            models_dir=models_dir,
            min_rows=min_rows,
            max_age_days=max_age_days,
            now=now,
            max_point_median_gap=max_point_median_gap,
        )

    try:
        frame = pd.read_parquet(forecast_path)
    except Exception:
        # Preserve the canonical error codes for unreadable artifacts.
        return validate_forecast_artifact(
            forecast_path,
            models_dir=models_dir,
            min_rows=min_rows,
            max_age_days=max_age_days,
            now=now,
            max_point_median_gap=max_point_median_gap,
        )

    missing = sorted(FORECAST_REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        return validate_forecast_artifact(
            forecast_path,
            models_dir=models_dir,
            min_rows=min_rows,
            max_age_days=max_age_days,
            now=now,
            max_point_median_gap=max_point_median_gap,
        )

    issues: list[ValidationIssue] = []
    if len(frame) < min_rows:
        _issue(
            issues,
            forecast_path,
            "insufficient_forecast_rows",
            f"found {len(frame)} rows; require at least {min_rows}",
        )

    duplicate_count = int(
        frame.duplicated(
            ["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]
        ).sum()
    )
    if duplicate_count:
        _issue(
            issues,
            forecast_path,
            "duplicate_serving_keys",
            f"found {duplicate_count} duplicate serving-key rows",
        )

    blank_symbols = int(
        frame["act_symbol"].fillna("").astype(str).str.strip().eq("").sum()
    )
    if blank_symbols:
        _issue(
            issues,
            forecast_path,
            "blank_forecast_symbols",
            f"found {blank_symbols} rows without a symbol",
        )

    bundle_ids = sorted(
        value
        for value in frame["model_bundle_id"].dropna().astype(str).str.strip().unique()
        if value
    )
    if len(bundle_ids) != 1:
        _issue(
            issues,
            forecast_path,
            "mixed_model_bundles",
            f"forecast snapshot must reference exactly one model bundle; found {bundle_ids}",
        )
    manifest_path = models_dir / "manifest.json"
    if manifest_path.exists() and len(bundle_ids) == 1:
        try:
            manifest_bundle_id = str(_load_json_object(manifest_path)["bundle_id"])
            if bundle_ids[0] != manifest_bundle_id:
                raise ValueError(
                    f"forecast references {bundle_ids[0]} but models_dir is {manifest_bundle_id}"
                )
        except Exception as exc:
            _issue(
                issues,
                forecast_path,
                "model_bundle_handoff_mismatch",
                str(exc),
            )

    strict_mask = _option_evidence_mask(frame)
    strict_rows = frame.loc[strict_mask].copy()
    ml_only_rows = frame.loc[~strict_mask].copy()

    strict_report: dict[str, Any] | None = None
    if not strict_rows.empty:
        # The existing validator is the source of truth for every row that
        # carries option evidence. Temporary serialization preserves the exact
        # production dataframe types and keeps all current quote gates intact.
        with tempfile.TemporaryDirectory(prefix="quantiv-strict-forecast-") as temp_dir:
            strict_path = Path(temp_dir) / forecast_path.name
            strict_rows.to_parquet(strict_path, index=False)
            strict_report = validate_forecast_artifact(
                strict_path,
                models_dir=models_dir,
                min_rows=1,
                max_age_days=max_age_days,
                now=now,
                max_point_median_gap=max_point_median_gap,
            )

    ml_only_issues, ml_only_summary = _validate_ml_only_rows(
        ml_only_rows,
        artifact=forecast_path,
        models_dir=models_dir,
        max_age_days=max_age_days,
        now=now,
        max_point_median_gap=max_point_median_gap,
    )
    issues.extend(ml_only_issues)

    if issues:
        raise PipelineValidationError(issues)

    earnings_dates = pd.to_datetime(frame["earnings_date"], errors="coerce")
    snapshot_dates = pd.to_datetime(frame["snapshot_date"], errors="coerce")
    scored_at = pd.to_datetime(frame["scored_at"], errors="coerce", utc=True)
    horizons = sorted(
        int(value)
        for value in pd.to_numeric(frame["model_horizon"], errors="coerce").dropna().unique()
        if float(value) > 0 and float(value) % 1 == 0
    )

    return {
        "status": "passed",
        "stage": "forecasts",
        "artifact": str(forecast_path),
        "rows": len(frame),
        "symbols": int(frame["act_symbol"].nunique()),
        "events": int(
            frame[["act_symbol", "earnings_date"]].drop_duplicates().shape[0]
        ),
        "horizons": horizons,
        "data_window": {
            "snapshot_min": (
                snapshot_dates.min().date().isoformat()
                if snapshot_dates.notna().any()
                else None
            ),
            "snapshot_max": (
                snapshot_dates.max().date().isoformat()
                if snapshot_dates.notna().any()
                else None
            ),
            "earnings_min": (
                earnings_dates.min().date().isoformat()
                if earnings_dates.notna().any()
                else None
            ),
            "earnings_max": (
                earnings_dates.max().date().isoformat()
                if earnings_dates.notna().any()
                else None
            ),
            "scored_at_min": (
                scored_at.min().isoformat() if scored_at.notna().any() else None
            ),
            "scored_at_max": (
                scored_at.max().isoformat() if scored_at.notna().any() else None
            ),
        },
        "reconciliation": {
            "duplicate_serving_keys": duplicate_count,
            "blank_symbols": blank_symbols,
            "strict_option_rows": len(strict_rows),
            **ml_only_summary,
            "strict_reconciliation": (
                strict_report.get("reconciliation") if strict_report else None
            ),
        },
    }
