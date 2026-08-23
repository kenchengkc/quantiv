"""Fail-closed validation gates for the earnings-forecast ML pipeline."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from ml.model_artifact import load_native_model, point_model_name, quantile_model_name
from ml.training_split import chronological_train_val_split

DEFAULT_HORIZONS = (1, 2, 3, 7, 14, 21)
QUANTILE_LEVELS = (10, 25, 50, 75, 90)


@dataclass(frozen=True)
class ValidationIssue:
    stage: str
    artifact: str
    code: str
    message: str


class PipelineValidationError(RuntimeError):
    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = list(issues)
        super().__init__(
            f"ML pipeline validation failed with {len(self.issues)} issue(s)"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "failed",
            "issue_count": len(self.issues),
            "issues": [asdict(issue) for issue in self.issues],
        }


def _issue(
    issues: list[ValidationIssue],
    stage: str,
    artifact: Path | str,
    code: str,
    message: str,
) -> None:
    issues.append(ValidationIssue(stage, str(artifact), code, message))


def _finish(issues: list[ValidationIssue], summary: dict[str, Any]) -> dict[str, Any]:
    if issues:
        raise PipelineValidationError(issues)
    return {"status": "passed", **summary}


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def validate_training_artifacts(
    training_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    min_rows: int = 1_000,
    min_symbols: int = 20,
    min_history_days: int = 365,
    train_frac: float = 0.75,
    purge_days: int = 5,
) -> dict[str, Any]:
    """Validate feature/target artifacts before spending time on training."""
    issues: list[ValidationIssue] = []
    horizon_summaries: dict[str, Any] = {}

    for horizon in horizons:
        path = training_dir / f"training_T{horizon}.parquet"
        metadata_path = training_dir / f"metadata_T{horizon}.json"
        if not path.exists():
            _issue(
                issues,
                "training",
                path,
                "missing_training_artifact",
                "file does not exist",
            )
            continue
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            _issue(issues, "training", path, "unreadable_training_artifact", str(exc))
            continue

        required = {"target", "__earnings_date", "__symbol"}
        missing = sorted(required - set(frame.columns))
        if missing:
            _issue(
                issues,
                "training",
                path,
                "missing_training_columns",
                f"missing required columns: {missing}",
            )
            continue
        if len(frame) < min_rows:
            _issue(
                issues,
                "training",
                path,
                "insufficient_training_rows",
                f"found {len(frame):,}; require at least {min_rows:,}",
            )

        feature_cols = [
            column
            for column in frame.columns
            if column != "target" and not column.startswith("__")
        ]
        if len(feature_cols) < 10:
            _issue(
                issues,
                "training",
                path,
                "insufficient_feature_count",
                f"found {len(feature_cols)} model features; require at least 10",
            )
        non_numeric = [
            column
            for column in feature_cols
            if not pd.api.types.is_numeric_dtype(frame[column])
        ]
        if non_numeric:
            _issue(
                issues,
                "training",
                path,
                "non_numeric_model_features",
                f"non-numeric model features: {non_numeric}",
            )
        else:
            all_null = [
                column for column in feature_cols if frame[column].notna().sum() == 0
            ]
            if all_null:
                _issue(
                    issues,
                    "training",
                    path,
                    "all_null_model_features",
                    f"features contain no usable values: {all_null}",
                )
            infinity_count = int(
                np.isinf(frame[feature_cols].to_numpy(dtype=float)).sum()
            )
            if infinity_count:
                _issue(
                    issues,
                    "training",
                    path,
                    "infinite_model_features",
                    f"found {infinity_count:,} infinite feature values",
                )

        target = pd.to_numeric(frame["target"], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(target).all():
            _issue(
                issues,
                "training",
                path,
                "invalid_training_targets",
                "targets must all be finite",
            )
        elif (target < 0).any() or (target > 5).any():
            _issue(
                issues,
                "training",
                path,
                "out_of_range_training_targets",
                "absolute-move targets must be between 0 and 5 (0% to 500%)",
            )

        parsed_dates = pd.to_datetime(frame["__earnings_date"], errors="coerce")
        if parsed_dates.isna().any():
            _issue(
                issues,
                "training",
                path,
                "invalid_earnings_dates",
                f"found {int(parsed_dates.isna().sum())} invalid date value(s)",
            )
            continue
        history_days = int((parsed_dates.max() - parsed_dates.min()).days)
        if history_days < min_history_days:
            _issue(
                issues,
                "training",
                path,
                "insufficient_history_span",
                f"history spans {history_days} days; require at least {min_history_days}",
            )
        symbol_count = int(frame["__symbol"].nunique(dropna=True))
        blank_symbols = int(
            frame["__symbol"].fillna("").astype(str).str.strip().eq("").sum()
        )
        if blank_symbols:
            _issue(
                issues,
                "training",
                path,
                "blank_training_symbols",
                f"found {blank_symbols} rows without a symbol",
            )
        if symbol_count < min_symbols:
            _issue(
                issues,
                "training",
                path,
                "insufficient_symbol_coverage",
                f"found {symbol_count} symbols; require at least {min_symbols}",
            )
        duplicate_count = int(frame.duplicated(["__symbol", "__earnings_date"]).sum())
        if duplicate_count:
            _issue(
                issues,
                "training",
                path,
                "duplicate_training_events",
                f"found {duplicate_count:,} duplicate symbol/event-date rows",
            )
        if "horizon" in frame.columns:
            invalid_horizon = int(
                (pd.to_numeric(frame["horizon"], errors="coerce") != horizon).sum()
            )
            if invalid_horizon:
                _issue(
                    issues,
                    "training",
                    path,
                    "horizon_mismatch",
                    f"found {invalid_horizon:,} rows not labeled T-{horizon}",
                )

        try:
            train, validation, split = chronological_train_val_split(
                frame,
                train_frac=train_frac,
                purge_days=purge_days,
            )
        except ValueError as exc:
            _issue(issues, "training", path, "invalid_validation_split", str(exc))
            split = None
            train = validation = frame.iloc[0:0]

        if not metadata_path.exists():
            _issue(
                issues,
                "training",
                metadata_path,
                "missing_feature_metadata",
                "feature-engineering metadata does not exist",
            )
        else:
            try:
                metadata = _load_json(metadata_path)
                if int(metadata.get("n_samples", -1)) != len(frame):
                    _issue(
                        issues,
                        "training",
                        metadata_path,
                        "feature_metadata_row_mismatch",
                        f"metadata n_samples={metadata.get('n_samples')} but parquet has {len(frame)}",
                    )
                if list(metadata.get("feature_cols") or []) != feature_cols:
                    _issue(
                        issues,
                        "training",
                        metadata_path,
                        "feature_metadata_schema_mismatch",
                        "metadata feature_cols does not exactly match parquet model columns",
                    )
            except Exception as exc:
                _issue(
                    issues,
                    "training",
                    metadata_path,
                    "invalid_feature_metadata",
                    str(exc),
                )

        horizon_summaries[str(horizon)] = {
            "rows": len(frame),
            "features": len(feature_cols),
            "symbols": symbol_count,
            "history_days": history_days,
            "train_rows": len(train),
            "validation_rows": len(validation),
            "split": split,
        }

    return _finish(
        issues,
        {
            "stage": "training",
            "horizons": horizon_summaries,
            "artifact_dir": str(training_dir),
        },
    )


def _feature_names(estimator: Any) -> list[str]:
    feature_name = getattr(estimator, "feature_name", None)
    return list(feature_name()) if callable(feature_name) else []


def validate_model_artifacts(
    models_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    training_dir: Path | None = None,
    min_train_rows: int = 1_000,
    min_validation_rows: int = 200,
    coverage_tolerance: float = 0.08,
    max_crossing_rate: float = 0.20,
    max_negative_rate: float = 0.05,
) -> dict[str, Any]:
    """Validate model bundles, schemas, holdout metrics, and smoke inference."""
    issues: list[ValidationIssue] = []
    horizon_summaries: dict[str, Any] = {}

    for horizon in horizons:
        metadata_path = models_dir / f"metadata_T{horizon}.json"
        point_path = models_dir / point_model_name(horizon)
        if not metadata_path.exists():
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_model_metadata",
                "file does not exist",
            )
            continue
        try:
            metadata = _load_json(metadata_path)
        except Exception as exc:
            _issue(issues, "models", metadata_path, "invalid_model_metadata", str(exc))
            continue

        required_metrics = (
            "n_train",
            "n_val",
            "val_mae",
            "baseline_straddle_mae",
            "coverage_80",
            "coverage_50",
            "quantile_crossing_rate_raw",
            "quantile_negative_rate_raw",
        )
        missing_metrics = [
            key for key in required_metrics if not _finite_number(metadata.get(key))
        ]
        if missing_metrics:
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_validation_metrics",
                f"missing or non-finite metrics: {missing_metrics}",
            )
        else:
            if (
                int(metadata["n_train"]) < min_train_rows
                or int(metadata["n_val"]) < min_validation_rows
            ):
                _issue(
                    issues,
                    "models",
                    metadata_path,
                    "insufficient_holdout_rows",
                    f"train/validation rows are {metadata['n_train']}/{metadata['n_val']}",
                )
            if float(metadata["val_mae"]) >= float(metadata["baseline_straddle_mae"]):
                _issue(
                    issues,
                    "models",
                    metadata_path,
                    "model_fails_baseline",
                    "validation MAE does not beat the straddle baseline",
                )
            for key, target in (("coverage_80", 0.80), ("coverage_50", 0.50)):
                if abs(float(metadata[key]) - target) > coverage_tolerance:
                    _issue(
                        issues,
                        "models",
                        metadata_path,
                        "interval_miscalibration",
                        f"{key}={metadata[key]:.3f}; target={target:.2f} ± {coverage_tolerance:.2f}",
                    )
            for quantile in QUANTILE_LEVELS:
                key = f"q{quantile:02d}_coverage"
                value = metadata.get(key)
                target = quantile / 100
                if (
                    not _finite_number(value)
                    or abs(float(value) - target) > coverage_tolerance
                ):
                    _issue(
                        issues,
                        "models",
                        metadata_path,
                        "quantile_miscalibration",
                        f"{key}={value}; target={target:.2f} ± {coverage_tolerance:.2f}",
                    )
            if float(metadata["quantile_crossing_rate_raw"]) > max_crossing_rate:
                _issue(
                    issues,
                    "models",
                    metadata_path,
                    "excessive_quantile_crossing",
                    f"raw crossing rate {metadata['quantile_crossing_rate_raw']:.1%} exceeds {max_crossing_rate:.1%}",
                )
            if float(metadata["quantile_negative_rate_raw"]) > max_negative_rate:
                _issue(
                    issues,
                    "models",
                    metadata_path,
                    "excessive_negative_quantiles",
                    f"raw negative rate {metadata['quantile_negative_rate_raw']:.1%} exceeds {max_negative_rate:.1%}",
                )

        split = metadata.get("validation_split")
        if not isinstance(split, dict):
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_split_audit",
                "validation_split audit metadata is required",
            )
        else:
            try:
                train_end = pd.Timestamp(split["train_end"])
                validation_start = pd.Timestamp(split["validation_start"])
                split_purge_days = int(split["purge_days"])
                if (validation_start - train_end).days <= split_purge_days:
                    raise ValueError(
                        "train/validation date ranges violate the purge window"
                    )
                if int(split["rows_train"]) != int(metadata.get("n_train", -1)):
                    raise ValueError("split rows_train does not match n_train")
                if int(split["rows_validation"]) != int(metadata.get("n_val", -1)):
                    raise ValueError("split rows_validation does not match n_val")
                expected_total = (
                    int(split["rows_train"])
                    + int(split["rows_purged"])
                    + int(split["rows_validation"])
                )
                if int(split["rows_total"]) != expected_total:
                    raise ValueError("split row counts do not add up to rows_total")
            except (KeyError, TypeError, ValueError) as exc:
                _issue(issues, "models", metadata_path, "invalid_split_audit", str(exc))

        feature_cols = list(metadata.get("feature_cols") or [])
        if not feature_cols:
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_model_schema",
                "feature_cols must be a non-empty ordered list",
            )
            continue

        feature_reference = metadata.get("feature_reference")
        if not isinstance(feature_reference, dict) or set(feature_reference) != set(feature_cols):
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_feature_drift_reference",
                "feature_reference must cover the exact deployed feature schema",
            )
        residual_reference = metadata.get("residual_reference")
        if not isinstance(residual_reference, dict) or not all(
            _finite_number(residual_reference.get(key))
            for key in ("rows", "mean", "std", "p05", "median", "p95")
        ):
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_residual_drift_reference",
                "residual_reference must contain finite distribution statistics",
            )
        slices = metadata.get("validation_slices")
        required_slices = {"sector", "volatility_regime", "liquidity", "dte"}
        if not isinstance(slices, dict) or not required_slices <= set(slices):
            _issue(
                issues,
                "models",
                metadata_path,
                "missing_calibration_slices",
                f"validation_slices must include {sorted(required_slices)}",
            )
        else:
            for dimension in sorted(required_slices):
                cohorts = slices[dimension].get("cohorts") if isinstance(slices[dimension], dict) else None
                if not isinstance(cohorts, dict) or not cohorts:
                    _issue(
                        issues,
                        "models",
                        metadata_path,
                        "empty_calibration_slice",
                        f"validation_slices.{dimension} has no cohorts",
                    )
        walk_forward = metadata.get("walk_forward_validation")
        if not isinstance(walk_forward, dict) or walk_forward.get("status") != "passed":
            _issue(
                issues,
                "models",
                metadata_path,
                "walk_forward_gate_failed",
                "a passing mandatory walk-forward validation is required",
            )
        else:
            try:
                if int(walk_forward["fold_count"]) < 3:
                    raise ValueError("at least three walk-forward folds are required")
                if int(walk_forward["purge_days"]) < 1:
                    raise ValueError("walk-forward validation must use a purge window")
                if float(walk_forward["model_mae"]) >= float(
                    walk_forward["baseline_straddle_mae"]
                ):
                    raise ValueError("walk-forward MAE does not beat the straddle baseline")
            except (KeyError, TypeError, ValueError) as exc:
                _issue(
                    issues,
                    "models",
                    metadata_path,
                    "invalid_walk_forward_audit",
                    str(exc),
                )

        sample: pd.DataFrame | None = None
        if training_dir is not None:
            training_path = training_dir / f"training_T{horizon}.parquet"
            if training_path.exists():
                try:
                    training_frame = pd.read_parquet(
                        training_path, columns=feature_cols
                    )
                    sample = training_frame.tail(1).replace([np.inf, -np.inf], np.nan)
                except Exception as exc:
                    _issue(
                        issues,
                        "models",
                        training_path,
                        "invalid_inference_sample",
                        str(exc),
                    )

        artifact_paths = [("point", point_path)] + [
            (f"q{quantile:02d}", models_dir / quantile_model_name(horizon, quantile))
            for quantile in QUANTILE_LEVELS
        ]
        loaded_count = 0
        for label, artifact_path in artifact_paths:
            if not artifact_path.exists():
                _issue(
                    issues,
                    "models",
                    artifact_path,
                    "missing_model_artifact",
                    f"{label} artifact does not exist",
                )
                continue
            try:
                estimator = load_native_model(artifact_path)
                artifact_features = _feature_names(estimator)
                if artifact_features != feature_cols:
                    raise ValueError(
                        "estimator feature order does not match metadata feature_cols"
                    )
                if sample is not None:
                    prediction = np.asarray(estimator.predict(sample), dtype=float)
                    if prediction.shape != (1,) or not np.isfinite(prediction).all():
                        raise ValueError(
                            "smoke inference did not produce one finite prediction"
                        )
                loaded_count += 1
            except Exception as exc:
                _issue(
                    issues, "models", artifact_path, "invalid_model_artifact", str(exc)
                )

        horizon_summaries[str(horizon)] = {
            "features": len(feature_cols),
            "artifacts_loaded": loaded_count,
            "val_mae": metadata.get("val_mae"),
            "baseline_straddle_mae": metadata.get("baseline_straddle_mae"),
            "coverage_80": metadata.get("coverage_80"),
            "coverage_50": metadata.get("coverage_50"),
            "quantile_crossing_rate_raw": metadata.get("quantile_crossing_rate_raw"),
        }

    return _finish(
        issues,
        {
            "stage": "models",
            "horizons": horizon_summaries,
            "artifact_dir": str(models_dir),
        },
    )


def _strict_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError("feature_vector must be a JSON string")

    def reject_constant(token: str) -> None:
        raise ValueError(f"non-standard JSON constant {token}")

    payload = json.loads(value, parse_constant=reject_constant)
    if not isinstance(payload, dict):
        raise ValueError("feature_vector must decode to an object")
    return payload


def latest_forecast_path(forecast_dir: Path) -> Path | None:
    paths = sorted(forecast_dir.glob("forecasts_????-??-??.parquet"))
    return paths[-1] if paths else None


def validate_forecast_artifact(
    forecast_path: Path,
    *,
    models_dir: Path,
    min_rows: int = 1,
    max_age_days: int = 2,
    now: datetime | None = None,
    max_point_median_gap: float = 0.05,
) -> dict[str, Any]:
    """Validate the scored handoff before import, upload, or live serving."""
    issues: list[ValidationIssue] = []
    if not forecast_path.exists():
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "missing_forecast_artifact",
            "file does not exist",
        )
        return _finish(issues, {})
    try:
        frame = pd.read_parquet(forecast_path)
    except Exception as exc:
        _issue(
            issues, "forecasts", forecast_path, "unreadable_forecast_artifact", str(exc)
        )
        return _finish(issues, {})

    required = {
        "act_symbol",
        "earnings_date",
        "snapshot_date",
        "model_horizon",
        "spot_price",
        "atm_iv",
        "em_math_pct",
        "em_ml_pct",
        "em_ml_abs",
        "correction_factor",
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
        "scored_at",
        "feature_vector",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "missing_forecast_columns",
            f"missing required columns: {missing}",
        )
        return _finish(issues, {})
    if len(frame) < min_rows:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "insufficient_forecast_rows",
            f"found {len(frame)} rows; require at least {min_rows}",
        )

    key_cols = ["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]
    duplicate_count = int(frame.duplicated(key_cols).sum())
    if duplicate_count:
        _issue(
            issues,
            "forecasts",
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
            "forecasts",
            forecast_path,
            "blank_forecast_symbols",
            f"found {blank_symbols} rows without a symbol",
        )

    horizon_values = pd.to_numeric(frame["model_horizon"], errors="coerce")
    invalid_horizons = int(
        (
            horizon_values.isna() | (horizon_values <= 0) | (horizon_values % 1 != 0)
        ).sum()
    )
    if invalid_horizons:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "invalid_model_horizons",
            f"found {invalid_horizons} non-positive or non-integer model horizons",
        )

    numeric_cols = [
        "spot_price",
        "atm_iv",
        "em_math_pct",
        "em_ml_pct",
        "em_ml_abs",
        "correction_factor",
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
    ]
    numeric = (
        frame[numeric_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    )
    non_finite_values = int((~np.isfinite(numeric)).sum())
    invalid_market_input_rows = 0
    out_of_range_move_rows = 0
    crossing_count = 0
    outside_count = 0
    median_gap: float | None = None
    absolute_mismatches = 0
    correction_mismatches = 0
    if non_finite_values:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "non_finite_forecast_values",
            f"found {non_finite_values} non-finite core forecast values",
        )
    else:
        invalid_market_input_rows = int(
            ((frame["spot_price"] <= 0) | (frame["atm_iv"] <= 0)).sum()
        )
        if invalid_market_input_rows:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "invalid_market_inputs",
                f"found {invalid_market_input_rows} rows without positive spot and ATM IV",
            )
        move_cols = ["em_math_pct", "em_ml_pct", "p10", "p25", "p50", "p75", "p90"]
        out_of_range_move_rows = int(
            ((frame[move_cols] < 0) | (frame[move_cols] > 3)).any(axis=1).sum()
        )
        if out_of_range_move_rows:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "out_of_range_move_forecasts",
                f"found {out_of_range_move_rows} rows outside the 0%–300% move range",
            )
        quantiles = frame[["p10", "p25", "p50", "p75", "p90"]].to_numpy(dtype=float)
        crossing_count = int(np.any(np.diff(quantiles, axis=1) < 0, axis=1).sum())
        if crossing_count:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "crossed_served_quantiles",
                f"found {crossing_count} rows with non-monotone quantiles",
            )
        outside_count = int(
            (
                (frame["em_ml_pct"] < frame["p10"])
                | (frame["em_ml_pct"] > frame["p90"])
            ).sum()
        )
        if outside_count:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "point_outside_forecast_band",
                f"found {outside_count} point estimates outside P10–P90",
            )
        median_gap = float((frame["em_ml_pct"] - frame["p50"]).abs().max())
        if median_gap > max_point_median_gap:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "point_median_divergence",
                f"maximum point/P50 gap {median_gap:.4f} exceeds {max_point_median_gap:.4f}",
            )
        expected_abs = frame["em_ml_pct"] * frame["spot_price"]
        absolute_mismatches = int(
            (~np.isclose(frame["em_ml_abs"], expected_abs, rtol=1e-6, atol=1e-8)).sum()
        )
        if absolute_mismatches:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "absolute_forecast_mismatch",
                f"found {absolute_mismatches} rows where ML $ move does not match pct × spot",
            )
        expected_correction = frame["em_ml_pct"] / frame["em_math_pct"].clip(
            lower=0.001
        )
        correction_mismatches = int(
            (
                ~np.isclose(
                    frame["correction_factor"],
                    expected_correction,
                    rtol=1e-6,
                    atol=1e-8,
                )
            ).sum()
        )
        if correction_mismatches:
            _issue(
                issues,
                "forecasts",
                forecast_path,
                "correction_factor_mismatch",
                f"found {correction_mismatches} rows with an inconsistent correction factor",
            )

    earnings_dates = pd.to_datetime(frame["earnings_date"], errors="coerce")
    snapshot_dates = pd.to_datetime(frame["snapshot_date"], errors="coerce")
    if earnings_dates.isna().any() or snapshot_dates.isna().any():
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "invalid_forecast_dates",
            "earnings_date and snapshot_date must be valid dates",
        )
    elif (snapshot_dates > earnings_dates).any():
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "post_event_feature_snapshot",
            "feature snapshots must not occur after the earnings event",
        )

    scored_at = pd.to_datetime(frame["scored_at"], errors="coerce", utc=True)
    if scored_at.isna().any():
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "invalid_scored_at",
            "scored_at must be parseable for every row",
        )
    elif len(frame):
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
                "forecasts",
                forecast_path,
                "stale_forecast_artifact",
                f"newest score is {age_days:.1f} days old; maximum is {max_age_days}",
            )

    metadata_by_horizon: dict[int, dict[str, Any]] = {}
    valid_horizons = horizon_values[
        horizon_values.notna() & (horizon_values > 0) & (horizon_values % 1 == 0)
    ].astype(int)
    for horizon in sorted(set(valid_horizons)):
        metadata_path = models_dir / f"metadata_T{horizon}.json"
        try:
            metadata = _load_json(metadata_path)
            if not list(metadata.get("feature_cols") or []):
                raise ValueError("model metadata has no feature_cols schema")
            metadata_by_horizon[horizon] = metadata
        except Exception as exc:
            _issue(
                issues,
                "forecasts",
                metadata_path,
                "missing_forecast_model_metadata",
                str(exc),
            )

    invalid_vectors = 0
    schema_mismatches = 0
    invalid_feature_values = 0
    iv_formula_mismatches = 0
    straddle_mismatches = 0
    for _, row in frame.iterrows():
        try:
            vector = _strict_json_object(row["feature_vector"])
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid_vectors += 1
            continue
        try:
            horizon = int(row["model_horizon"])
        except (TypeError, ValueError, OverflowError):
            invalid_vectors += 1
            continue
        metadata = metadata_by_horizon.get(horizon)
        expected_features = list(metadata.get("feature_cols") or []) if metadata else []
        # The persisted vector intentionally includes extra diagnostic inputs
        # for auditability. It must contain every ordered model input, but the
        # diagnostics do not need to be part of the estimator schema.
        if expected_features and not set(expected_features).issubset(vector):
            schema_mismatches += 1
        if any(
            value is not None and not _finite_number(value) for value in vector.values()
        ):
            invalid_feature_values += 1
        try:
            atm_iv = float(vector["atm_iv"])
            dte = float(vector["dte"])
            em_iv_pct = float(vector["em_iv_pct"])
            expected_iv_move = atm_iv * math.sqrt(max(dte, 0.0) / 365.0)
            if not math.isclose(
                em_iv_pct, expected_iv_move, rel_tol=1e-6, abs_tol=1e-9
            ):
                iv_formula_mismatches += 1
        except (KeyError, TypeError, ValueError):
            iv_formula_mismatches += 1
        try:
            if not math.isclose(
                float(vector["straddle_pct"]),
                float(row["em_math_pct"]),
                rel_tol=1e-6,
                abs_tol=1e-9,
            ):
                straddle_mismatches += 1
        except (KeyError, TypeError, ValueError):
            straddle_mismatches += 1

    if invalid_vectors:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "invalid_feature_vectors",
            f"found {invalid_vectors} invalid strict-JSON feature vectors",
        )
    if schema_mismatches:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "feature_vector_schema_mismatch",
            f"found {schema_mismatches} vectors missing model metadata features",
        )
    if invalid_feature_values:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "invalid_feature_values",
            f"found {invalid_feature_values} vectors with non-numeric feature values",
        )
    if iv_formula_mismatches:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "iv_expected_move_mismatch",
            f"found {iv_formula_mismatches} rows where IV × √(DTE/365) does not match em_iv_pct",
        )
    if straddle_mismatches:
        _issue(
            issues,
            "forecasts",
            forecast_path,
            "straddle_handoff_mismatch",
            f"found {straddle_mismatches} rows where feature and output straddle moves differ",
        )

    return _finish(
        issues,
        {
            "stage": "forecasts",
            "artifact": str(forecast_path),
            "rows": len(frame),
            "symbols": int(frame["act_symbol"].nunique()),
            "events": int(
                frame[["act_symbol", "earnings_date"]].drop_duplicates().shape[0]
            ),
            "horizons": sorted(metadata_by_horizon),
            "data_window": {
                "snapshot_min": snapshot_dates.min().date().isoformat()
                if snapshot_dates.notna().any()
                else None,
                "snapshot_max": snapshot_dates.max().date().isoformat()
                if snapshot_dates.notna().any()
                else None,
                "earnings_min": earnings_dates.min().date().isoformat()
                if earnings_dates.notna().any()
                else None,
                "earnings_max": earnings_dates.max().date().isoformat()
                if earnings_dates.notna().any()
                else None,
                "scored_at_min": scored_at.min().isoformat()
                if scored_at.notna().any()
                else None,
                "scored_at_max": scored_at.max().isoformat()
                if scored_at.notna().any()
                else None,
            },
            "reconciliation": {
                "duplicate_serving_keys": duplicate_count,
                "blank_symbols": blank_symbols,
                "invalid_horizons": invalid_horizons,
                "non_finite_values": non_finite_values,
                "invalid_market_input_rows": invalid_market_input_rows,
                "out_of_range_move_rows": out_of_range_move_rows,
                "quantile_crossings": crossing_count,
                "point_outside_band": outside_count,
                "max_point_median_gap": median_gap,
                "absolute_move_mismatches": absolute_mismatches,
                "correction_factor_mismatches": correction_mismatches,
                "invalid_feature_vectors": invalid_vectors,
                "feature_schema_mismatches": schema_mismatches,
                "invalid_feature_values": invalid_feature_values,
                "iv_formula_mismatches": iv_formula_mismatches,
                "straddle_handoff_mismatches": straddle_mismatches,
            },
        },
    )
