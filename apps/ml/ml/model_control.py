"""Champion/challenger evaluation, shadow scoring, and drift diagnostics."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from ml.model_artifact import load_native_model, point_model_name, quantile_model_name
from ml.model_bundle import DEFAULT_HORIZONS, DEFAULT_QUANTILES, verify_bundle_dir
from ml.quantiles import rearrange_quantile_array


def _metadata(bundle_dir: Path, horizon: int) -> dict[str, Any]:
    payload = json.loads((bundle_dir / f"metadata_T{horizon}.json").read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"T-{horizon} metadata is not an object")
    return payload


def _models(bundle_dir: Path, horizon: int) -> tuple[Any, dict[int, Any], list[str]]:
    metadata = _metadata(bundle_dir, horizon)
    features = list(metadata.get("feature_cols") or [])
    point = load_native_model(bundle_dir / point_model_name(horizon))
    quantiles = {
        quantile: load_native_model(bundle_dir / quantile_model_name(horizon, quantile))
        for quantile in DEFAULT_QUANTILES
    }
    if list(point.feature_name()) != features or any(
        list(model.feature_name()) != features for model in quantiles.values()
    ):
        raise ValueError(f"T-{horizon} bundle schema mismatch")
    return point, quantiles, features


def score_bundle_frame(
    bundle_dir: Path,
    horizon: int,
    frame: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    point, quantiles, features = _models(bundle_dir, horizon)
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise ValueError(f"T-{horizon} comparison data lacks features: {missing}")
    X = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    point_prediction = np.clip(point.predict(X), 0.0, None)
    quantile_prediction = rearrange_quantile_array(
        np.column_stack([quantiles[q].predict(X) for q in DEFAULT_QUANTILES])
    )
    return np.asarray(point_prediction, dtype=float), quantile_prediction


def _coverage_error(actual: np.ndarray, quantiles: np.ndarray) -> tuple[float, dict[str, float]]:
    coverage_80 = float(np.mean((actual >= quantiles[:, 0]) & (actual <= quantiles[:, 4])))
    coverage_50 = float(np.mean((actual >= quantiles[:, 1]) & (actual <= quantiles[:, 3])))
    quantile_coverage = {
        f"q{quantile:02d}_coverage": float(np.mean(actual <= quantiles[:, index]))
        for index, quantile in enumerate(DEFAULT_QUANTILES)
    }
    error = abs(coverage_80 - 0.80) + abs(coverage_50 - 0.50) + sum(
        abs(quantile_coverage[f"q{quantile:02d}_coverage"] - quantile / 100)
        for quantile in DEFAULT_QUANTILES
    ) / len(DEFAULT_QUANTILES)
    return error, {
        "coverage_80": coverage_80,
        "coverage_50": coverage_50,
        **quantile_coverage,
    }


def compare_on_common_holdout(
    candidate_dir: Path,
    champion_dir: Path,
    training_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    max_mae_regression: float = 0.02,
    max_calibration_regression: float = 0.05,
) -> dict[str, Any]:
    """Score both bundles on the candidate's exact purged holdout rows."""
    verify_bundle_dir(candidate_dir)
    verify_bundle_dir(champion_dir)
    report: dict[str, Any] = {"status": "passed", "horizons": {}, "issues": []}
    total_rows = 0
    weighted_candidate = 0.0
    weighted_champion = 0.0
    weighted_baseline = 0.0
    for horizon in horizons:
        candidate_metadata = _metadata(candidate_dir, horizon)
        split = candidate_metadata.get("validation_split") or {}
        validation_start = pd.Timestamp(split["validation_start"])
        validation_end = pd.Timestamp(split["validation_end"])
        frame = pd.read_parquet(training_dir / f"training_T{horizon}.parquet")
        dates = pd.to_datetime(frame["__earnings_date"], errors="raise")
        holdout = frame.loc[(dates >= validation_start) & (dates <= validation_end)].copy()
        if len(holdout) < 200:
            report["issues"].append(f"T-{horizon} common holdout has only {len(holdout)} rows")
            report["status"] = "failed"
            continue
        actual = holdout["target"].to_numpy(dtype=float)
        candidate_point, candidate_quantiles = score_bundle_frame(candidate_dir, horizon, holdout)
        champion_point, champion_quantiles = score_bundle_frame(champion_dir, horizon, holdout)
        baseline = pd.to_numeric(holdout["straddle_pct"], errors="coerce").fillna(float(np.mean(actual)))
        candidate_mae = float(mean_absolute_error(actual, candidate_point))
        champion_mae = float(mean_absolute_error(actual, champion_point))
        baseline_mae = float(mean_absolute_error(actual, baseline))
        candidate_calibration, candidate_coverage = _coverage_error(actual, candidate_quantiles)
        champion_calibration, champion_coverage = _coverage_error(actual, champion_quantiles)
        issues: list[str] = []
        if candidate_mae >= baseline_mae:
            issues.append("candidate does not beat the straddle baseline")
        if candidate_mae > champion_mae * (1 + max_mae_regression):
            issues.append(
                f"candidate MAE regresses champion by {(candidate_mae / champion_mae - 1):.1%}"
            )
        if candidate_calibration > champion_calibration + max_calibration_regression:
            issues.append("candidate calibration materially regresses champion")
        if issues:
            report["status"] = "failed"
            report["issues"].extend(f"T-{horizon}: {issue}" for issue in issues)
        report["horizons"][str(horizon)] = {
            "rows": len(holdout),
            "candidate_mae": candidate_mae,
            "champion_mae": champion_mae,
            "baseline_straddle_mae": baseline_mae,
            "candidate_to_champion_ratio": candidate_mae / champion_mae,
            "candidate_calibration_error": candidate_calibration,
            "champion_calibration_error": champion_calibration,
            "candidate_coverage": candidate_coverage,
            "champion_coverage": champion_coverage,
            "status": "passed" if not issues else "failed",
            "issues": issues,
        }
        total_rows += len(holdout)
        weighted_candidate += candidate_mae * len(holdout)
        weighted_champion += champion_mae * len(holdout)
        weighted_baseline += baseline_mae * len(holdout)
    report["aggregate"] = {
        "rows": total_rows,
        "candidate_mae": weighted_candidate / total_rows if total_rows else None,
        "champion_mae": weighted_champion / total_rows if total_rows else None,
        "baseline_straddle_mae": weighted_baseline / total_rows if total_rows else None,
    }
    return report


def _parse_feature_vectors(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for value in frame["feature_vector"]:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise ValueError("forecast feature_vector is not a JSON object")
        records.append(dict(value))
    return pd.DataFrame(records, index=frame.index).apply(pd.to_numeric, errors="coerce")


def _population_stability_index(values: pd.Series, reference: Mapping[str, Any]) -> float | None:
    cuts = np.asarray(reference.get("cuts") or [], dtype=float)
    expected = np.asarray(reference.get("probabilities") or [], dtype=float)
    finite = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if not len(finite) or len(expected) != len(cuts) + 1:
        return None
    actual, _ = np.histogram(finite, bins=np.r_[-np.inf, cuts, np.inf])
    actual = actual / max(1, actual.sum())
    epsilon = 1e-6
    return float(np.sum((actual - expected) * np.log((actual + epsilon) / (expected + epsilon))))


def feature_drift_report(
    forecast_frame: pd.DataFrame,
    bundle_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    warning_psi: float = 0.20,
    critical_psi: float = 0.35,
    min_rows: int = 100,
) -> dict[str, Any]:
    vectors = _parse_feature_vectors(forecast_frame)
    report: dict[str, Any] = {"status": "passed", "horizons": {}}
    total_critical = 0
    total_features = 0
    for horizon in horizons:
        rows = forecast_frame["model_horizon"].astype(int) == horizon
        metadata = _metadata(bundle_dir, horizon)
        references = metadata.get("feature_reference") or {}
        horizon_rows = int(rows.sum())
        features: dict[str, Any] = {}
        critical = 0
        warning = 0
        for feature, reference in references.items():
            current = vectors.loc[rows, feature] if feature in vectors else pd.Series(np.nan, index=vectors.index[rows])
            psi = _population_stability_index(current, reference)
            missing_rate = float(current.isna().mean()) if len(current) else 1.0
            missing_delta = abs(missing_rate - float(reference.get("missing_rate", 0.0)))
            status = "passed"
            if horizon_rows < min_rows:
                status = "low_sample"
            elif (psi is not None and psi >= critical_psi) or missing_delta >= 0.25:
                status = "critical"
                critical += 1
            elif (psi is not None and psi >= warning_psi) or missing_delta >= 0.15:
                status = "warning"
                warning += 1
            features[feature] = {
                "psi": psi,
                "missing_rate": missing_rate,
                "training_missing_rate": reference.get("missing_rate"),
                "status": status,
            }
        if horizon_rows >= min_rows:
            total_features += len(features)
            total_critical += critical
        report["horizons"][str(horizon)] = {
            "rows": horizon_rows,
            "critical_features": critical,
            "warning_features": warning,
            "features": features,
        }
    critical_limit = max(2, math.ceil(total_features * 0.10))
    if total_critical >= critical_limit:
        report["status"] = "critical"
    elif any(row["warning_features"] or row["critical_features"] for row in report["horizons"].values()):
        report["status"] = "warning"
    report["critical_features"] = total_critical
    report["critical_limit"] = critical_limit
    return report


def shadow_score_report(
    candidate_forecasts: pd.DataFrame,
    champion_dir: Path,
) -> dict[str, Any]:
    vectors = _parse_feature_vectors(candidate_forecasts)
    report: dict[str, Any] = {"status": "passed", "horizons": {}, "issues": []}
    for horizon in DEFAULT_HORIZONS:
        mask = candidate_forecasts["model_horizon"].astype(int) == horizon
        if not mask.any():
            continue
        champion_point, _ = score_bundle_frame(champion_dir, horizon, vectors.loc[mask])
        candidate_point = candidate_forecasts.loc[mask, "em_ml_pct"].to_numpy(dtype=float)
        difference = np.abs(candidate_point - champion_point)
        payload = {
            "rows": int(mask.sum()),
            "mean_absolute_difference": float(np.mean(difference)),
            "p95_absolute_difference": float(np.quantile(difference, 0.95)),
            "max_absolute_difference": float(np.max(difference)),
            "candidate_mean": float(np.mean(candidate_point)),
            "champion_mean": float(np.mean(champion_point)),
        }
        if payload["p95_absolute_difference"] > 0.10:
            report["status"] = "failed"
            report["issues"].append(
                f"T-{horizon} p95 candidate/champion divergence exceeds 10 percentage points"
            )
        report["horizons"][str(horizon)] = payload
    return report


def monitoring_rows(
    forecast_frame: pd.DataFrame,
    bundle_dir: Path,
    *,
    bundle_id: str,
    role: str,
    use_served_predictions: bool = False,
) -> pd.DataFrame:
    vectors = _parse_feature_vectors(forecast_frame)
    rows: list[pd.DataFrame] = []
    for horizon in DEFAULT_HORIZONS:
        mask = forecast_frame["model_horizon"].astype(int) == horizon
        if not mask.any():
            continue
        selected = forecast_frame.loc[mask]
        if use_served_predictions:
            points = selected["em_ml_pct"].to_numpy(dtype=float)
            quantiles = selected[[f"p{q:02d}" for q in DEFAULT_QUANTILES]].to_numpy(dtype=float)
        else:
            points, quantiles = score_bundle_frame(bundle_dir, horizon, vectors.loc[mask])
        output = selected[
            ["act_symbol", "earnings_date", "snapshot_date", "model_horizon", "em_math_pct"]
        ].copy()
        output["bundle_id"] = bundle_id
        output["role"] = role
        output["prediction"] = points
        for index, quantile in enumerate(DEFAULT_QUANTILES):
            output[f"p{quantile:02d}"] = quantiles[:, index]
        rows.append(output)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def append_prediction_ledger(
    ledger_path: Path,
    rows: Sequence[pd.DataFrame],
    *,
    retention_days: int = 730,
) -> pd.DataFrame:
    frames = [frame for frame in rows if not frame.empty]
    if ledger_path.exists():
        frames.insert(0, pd.read_parquet(ledger_path))
    if not frames:
        return pd.DataFrame()
    ledger = pd.concat(frames, ignore_index=True)
    ledger["snapshot_date"] = pd.to_datetime(ledger["snapshot_date"], errors="raise")
    cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(
        days=retention_days
    )
    ledger = ledger.loc[ledger["snapshot_date"] >= cutoff]
    keys = [
        "bundle_id",
        "act_symbol",
        "earnings_date",
        "snapshot_date",
        "model_horizon",
    ]
    ledger = ledger.drop_duplicates(keys, keep="last").sort_values(keys)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    ledger.to_parquet(temporary, index=False)
    temporary.replace(ledger_path)
    return ledger


def _outcome_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    actual = frame["target"].to_numpy(dtype=float)
    prediction = frame["prediction"].to_numpy(dtype=float)
    baseline = frame["em_math_pct"].to_numpy(dtype=float)
    quantiles = frame[[f"p{q:02d}" for q in DEFAULT_QUANTILES]].to_numpy(dtype=float)
    residual = actual - prediction
    calibration_error, coverage = _coverage_error(actual, quantiles)
    return {
        "rows": len(frame),
        "mae": float(mean_absolute_error(actual, prediction)),
        "baseline_straddle_mae": float(mean_absolute_error(actual, baseline)),
        "residual_mean": float(np.mean(residual)),
        "residual_std": float(np.std(residual)),
        "calibration_error": calibration_error,
        **coverage,
    }


def _outcome_slices(frame: pd.DataFrame, *, min_rows: int = 20) -> dict[str, Any]:
    sector = frame.get("__sector", pd.Series("Unknown", index=frame.index)).fillna("Unknown")
    vix = pd.to_numeric(frame.get("vix_current"), errors="coerce")
    volatility = pd.Series(
        np.select(
            [vix < 15, vix.between(15, 25, inclusive="left"), vix >= 25],
            ["low VIX (<15)", "normal VIX (15–25)", "high VIX (≥25)"],
            default="VIX unavailable",
        ),
        index=frame.index,
    )
    volume = pd.to_numeric(frame.get("__dollar_volume"), errors="coerce")
    liquidity = pd.Series(
        np.select(
            [volume < 20_000_000, volume.between(20_000_000, 100_000_000, inclusive="left"), volume >= 100_000_000],
            ["lower", "medium", "higher"],
            default="unavailable",
        ),
        index=frame.index,
    )
    dte = pd.to_numeric(frame.get("dte"), errors="coerce")
    dte_bucket = pd.Series(
        np.select(
            [dte <= 3, dte.between(4, 7), dte.between(8, 14), dte.between(15, 30), dte > 30],
            ["0–3", "4–7", "8–14", "15–30", ">30"],
            default="unavailable",
        ),
        index=frame.index,
    )
    dimensions = {
        "sector": sector,
        "volatility_regime": volatility,
        "liquidity": liquidity,
        "dte": dte_bucket,
    }
    report: dict[str, Any] = {}
    for dimension, labels in dimensions.items():
        cohorts: dict[str, Any] = {}
        for label in sorted(labels.astype(str).unique()):
            cohort = frame.loc[labels.astype(str) == label]
            cohorts[label] = (
                _outcome_metrics(cohort)
                if len(cohort) >= min_rows
                else {"rows": len(cohort), "status": "low_sample"}
            )
        report[dimension] = cohorts
    return report


def evaluate_realized_outcomes(
    ledger: pd.DataFrame,
    training_dir: Path,
    champion_dir: Path,
    comparison_dir: Path,
    *,
    champion_id: str,
    comparison_id: str,
    min_common_rows: int = 30,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    realized_parts: list[pd.DataFrame] = []
    for horizon in horizons:
        path = training_dir / f"training_T{horizon}.parquet"
        columns = [
            "__symbol",
            "__earnings_date",
            "target",
            "__sector",
            "__dollar_volume",
            "dte",
            "vix_current",
        ]
        available = pd.read_parquet(path).columns
        frame = pd.read_parquet(path, columns=[column for column in columns if column in available])
        frame["model_horizon"] = horizon
        frame = frame.drop_duplicates(["__symbol", "__earnings_date"], keep="last")
        realized_parts.append(frame)
    realized = pd.concat(realized_parts, ignore_index=True).rename(
        columns={"__symbol": "act_symbol", "__earnings_date": "earnings_date"}
    )
    realized["earnings_date"] = pd.to_datetime(realized["earnings_date"], errors="raise")
    work = ledger.copy()
    work["earnings_date"] = pd.to_datetime(work["earnings_date"], errors="raise")
    work = work.merge(realized, on=["act_symbol", "earnings_date", "model_horizon"], how="inner")
    work = work.loc[work["snapshot_date"] < work["earnings_date"]]
    work = work.sort_values("snapshot_date").drop_duplicates(
        ["bundle_id", "act_symbol", "earnings_date", "model_horizon"], keep="last"
    )
    champion = work.loc[work["bundle_id"] == champion_id]
    comparison = work.loc[work["bundle_id"] == comparison_id]
    common_keys = ["act_symbol", "earnings_date", "model_horizon"]
    common = champion[common_keys].merge(comparison[common_keys], on=common_keys).drop_duplicates()
    champion_common = common.merge(champion, on=common_keys, how="inner")
    comparison_common = common.merge(comparison, on=common_keys, how="inner")
    report: dict[str, Any] = {
        "status": "insufficient_data",
        "common_rows": len(common),
        "minimum_common_rows": min_common_rows,
        "champion_bundle_id": champion_id,
        "comparison_bundle_id": comparison_id,
        "rollback_recommended": False,
    }
    if len(common) < min_common_rows:
        return report
    champion_metrics = _outcome_metrics(champion_common)
    comparison_metrics = _outcome_metrics(comparison_common)
    report.update(
        {
            "status": "passed",
            "champion": champion_metrics,
            "comparison": comparison_metrics,
            "calibration_slices": _outcome_slices(champion_common),
        }
    )
    # Compare production residuals with each horizon's validation reference.
    residual_alerts: list[dict[str, Any]] = []
    for horizon in horizons:
        cohort = champion_common.loc[champion_common["model_horizon"] == horizon]
        if len(cohort) < 20:
            continue
        observed = _outcome_metrics(cohort)
        reference = _metadata(champion_dir, horizon).get("residual_reference") or {}
        reference_std = float(reference.get("std") or 0.0)
        if reference_std and (
            abs(observed["residual_mean"] - float(reference.get("mean") or 0.0)) > 2 * reference_std
            or observed["residual_std"] > 1.75 * reference_std
        ):
            residual_alerts.append({"horizon": horizon, "observed": observed, "reference": reference})
    report["residual_drift_alerts"] = residual_alerts
    champion_worse_than_market = champion_metrics["mae"] > champion_metrics["baseline_straddle_mae"] * 1.05
    comparison_materially_better = comparison_metrics["mae"] < champion_metrics["mae"] * 0.95
    severe_undercoverage = champion_metrics["coverage_80"] < 0.65
    report["rollback_recommended"] = bool(
        comparison_materially_better and (champion_worse_than_market or severe_undercoverage)
    )
    report["rollback_reasons"] = {
        "champion_worse_than_market": champion_worse_than_market,
        "comparison_materially_better": comparison_materially_better,
        "severe_80pct_undercoverage": severe_undercoverage,
    }
    _ = comparison_dir  # verified by the caller; retained for an explicit audit signature.
    return report


__all__ = [
    "compare_on_common_holdout",
    "append_prediction_ledger",
    "evaluate_realized_outcomes",
    "feature_drift_report",
    "score_bundle_frame",
    "shadow_score_report",
    "monitoring_rows",
]
