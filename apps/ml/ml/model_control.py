"""Champion/challenger evaluation, shadow scoring, and drift diagnostics."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from ml.model_artifact import load_native_model, point_model_name, quantile_model_name
from ml.model_bundle import DEFAULT_HORIZONS, DEFAULT_QUANTILES, verify_bundle_dir
from ml.quantiles import rearrange_quantile_array
from ml.model_protocol import feature_protocol, target_protocol, SESSION_TARGET_PROTOCOL, LEGACY_FEATURE_PROTOCOL, LEGACY_TARGET_PROTOCOL


def _metadata(bundle_dir: Path, horizon: int) -> dict[str, Any]:
    payload = json.loads((bundle_dir / f"metadata_T{horizon}.json").read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"T-{horizon} metadata is not an object")
    return payload


def _models(bundle_dir: Path, horizon: int) -> tuple[Any, dict[int, Any], list[str]]:
    metadata = _metadata(bundle_dir, horizon)
    feature_protocol(metadata)
    target_protocol(metadata)
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
    declared = frame.attrs.get("feature_protocol", "quantiv.earnings-legacy.v1")
    if "feature_protocol" in frame and len(frame):
        protocols = set(frame["feature_protocol"].dropna().astype(str))
        if len(protocols) != 1:
            raise ValueError("comparison vectors have inconsistent feature protocols")
        declared = protocols.pop()
    if declared != feature_protocol(_metadata(bundle_dir, horizon)):
        raise ValueError("comparison vectors and model feature protocol differ")
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise ValueError(f"T-{horizon} comparison data lacks features: {missing}")
    X = (
        frame[features]
        .apply(pd.to_numeric, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
    )
    point_prediction = np.clip(point.predict(X), 0.0, None)
    quantile_prediction = rearrange_quantile_array(
        np.column_stack([quantiles[q].predict(X) for q in DEFAULT_QUANTILES])
    )
    return np.asarray(point_prediction, dtype=float), quantile_prediction


def _coverage_error(
    actual: np.ndarray, quantiles: np.ndarray
) -> tuple[float, dict[str, float]]:
    coverage_80 = float(
        np.mean((actual >= quantiles[:, 0]) & (actual <= quantiles[:, 4]))
    )
    coverage_50 = float(
        np.mean((actual >= quantiles[:, 1]) & (actual <= quantiles[:, 3]))
    )
    quantile_coverage = {
        f"q{quantile:02d}_coverage": float(np.mean(actual <= quantiles[:, index]))
        for index, quantile in enumerate(DEFAULT_QUANTILES)
    }
    error = (
        abs(coverage_80 - 0.80)
        + abs(coverage_50 - 0.50)
        + sum(
            abs(quantile_coverage[f"q{quantile:02d}_coverage"] - quantile / 100)
            for quantile in DEFAULT_QUANTILES
        )
        / len(DEFAULT_QUANTILES)
    )
    return error, {
        "coverage_80": coverage_80,
        "coverage_50": coverage_50,
        **quantile_coverage,
    }


def selection_exposure_end(metadata: Mapping[str, Any]) -> pd.Timestamp | None:
    """Conservatively exclude every label consumed by fit or model selection."""
    split = metadata.get("validation_split") or {}
    final = metadata.get("final_fit") or {}
    selection = metadata.get("selection_exposure") or {}
    values = [
        split.get("train_end"),
        split.get("validation_end"),
        final.get("end"),
        final.get("label_available_end"),
        selection.get("through_date"),
    ]
    dates = [pd.Timestamp(value) for value in values if value]
    if not dates or any(pd.isna(value) for value in dates):
        return None
    end = max(value.tz_localize(None) if value.tzinfo else value for value in dates)
    if not final.get("label_available_end") and not selection.get("through_date"):
        # Old metadata stores event dates, not when their labels became available.
        end += pd.Timedelta(days=max(5, int(metadata.get("label_lag_days") or 0)))
        if metadata.get("trained_at"):
            trained = pd.Timestamp(metadata["trained_at"])
            end = max(end, trained.tz_localize(None) if trained.tzinfo else trained)
    return end


def _paired_metrics(
    actual: np.ndarray,
    candidate_point: np.ndarray,
    champion_point: np.ndarray,
    candidate_quantiles: np.ndarray,
    champion_quantiles: np.ndarray,
    baseline: np.ndarray,
    *,
    min_rows: int = 200,
    max_mae_regression: float = 0.02,
    max_calibration_regression: float = 0.05,
    baseline_name: str = "straddle",
) -> dict[str, Any]:
    if len(actual) < min_rows:
        return {
            "status": "insufficient_data",
            "evaluation_complete": False,
            "failure_kind": "insufficient_data",
            "rows": len(actual),
            "issues": [
                f"unseen paired evidence has only {len(actual)} rows; require {min_rows}"
            ],
        }
    if not all(
        np.isfinite(values).all()
        for values in (
            actual,
            candidate_point,
            champion_point,
            candidate_quantiles,
            champion_quantiles,
        )
    ) or any((values < 0).any() for values in (actual, candidate_point, champion_point, candidate_quantiles, champion_quantiles)) or any(
        (np.diff(values, axis=1) < 0).any() for values in (candidate_quantiles, champion_quantiles)
    ):
        return {
            "status": "failed",
            "evaluation_complete": False,
            "failure_kind": "invalid_evidence",
            "rows": len(actual),
            "issues": ["paired targets, predictions, and quantiles must be finite, nonnegative and monotone"],
        }
    baseline_mask = np.isfinite(baseline) & (baseline >= 0)
    if int(baseline_mask.sum()) < min_rows:
        return {
            "status": "insufficient_data",
            "evaluation_complete": False,
            "failure_kind": "insufficient_data",
            "rows": len(actual),
            "baseline_rows": int(baseline_mask.sum()),
            "baseline_name": baseline_name,
            "issues": [
                f"matched {baseline_name} baseline has only {int(baseline_mask.sum())} rows; require {min_rows}"
            ],
        }
    candidate_mae = float(mean_absolute_error(actual, candidate_point))
    champion_mae = float(mean_absolute_error(actual, champion_point))
    cc, candidate_coverage = _coverage_error(actual, candidate_quantiles)
    hc, champion_coverage = _coverage_error(actual, champion_quantiles)
    baseline_mae = (
        float(mean_absolute_error(actual[baseline_mask], baseline[baseline_mask]))
        if baseline_mask.any()
        else None
    )
    issues = []
    if (
        baseline_mae is not None
        and mean_absolute_error(actual[baseline_mask], candidate_point[baseline_mask])
        >= baseline_mae
    ):
        issues.append(
            f"candidate does not beat the {baseline_name} baseline on comparable rows"
        )
    if candidate_mae > champion_mae * (1 + max_mae_regression):
        ratio = candidate_mae / champion_mae if champion_mae else float("inf")
        issues.append(f"candidate MAE regresses champion by {ratio - 1:.1%}")
    if cc > hc + max_calibration_regression:
        issues.append("candidate calibration materially regresses champion")
    return {
        "rows": len(actual),
        "candidate_mae": candidate_mae,
        "champion_mae": champion_mae,
        "candidate_to_champion_ratio": candidate_mae / champion_mae
        if champion_mae
        else None,
        "baseline_straddle_mae": baseline_mae if baseline_name == "straddle" else None,
        "baseline_mae": baseline_mae,
        "baseline_name": baseline_name,
        "baseline_rows": int(baseline_mask.sum()),
        "candidate_calibration_error": cc,
        "champion_calibration_error": hc,
        "candidate_coverage": candidate_coverage,
        "champion_coverage": champion_coverage,
        "status": "failed" if issues else "passed",
        "evaluation_complete": True,
        "failure_kind": "quality" if issues else None,
        "issues": issues,
    }


def _cohort_paired_metrics(
    actual: np.ndarray,
    candidate_point: np.ndarray,
    champion_point: np.ndarray,
    candidate_quantiles: np.ndarray,
    champion_quantiles: np.ndarray,
    straddle: np.ndarray,
    historical: np.ndarray,
    cohorts: np.ndarray,
    *,
    supported_cohorts: Sequence[str],
    min_rows: int = 200,
    max_mae_regression: float = 0.02,
    max_calibration_regression: float = 0.05,
) -> dict[str, Any]:
    """Every served cohort needs its own sufficient matched baseline evidence."""
    kwargs = {
        "min_rows": min_rows,
        "max_mae_regression": max_mae_regression,
        "max_calibration_regression": max_calibration_regression,
    }
    baseline = np.where(cohorts == "strict_options", straddle, historical)
    report = _paired_metrics(
        actual,
        candidate_point,
        champion_point,
        candidate_quantiles,
        champion_quantiles,
        baseline,
        baseline_name="matched_cohort",
        **kwargs,
    )
    report["cohorts"] = {}
    unsupported = set(cohorts) - set(supported_cohorts)
    if unsupported:
        report["status"] = "failed"
        report["issues"].append(
            f"paired evidence includes unsupported cohorts: {sorted(unsupported)}"
        )
    for name in supported_cohorts:
        mask = cohorts == name
        chosen = straddle if name == "strict_options" else historical
        payload = _paired_metrics(
            actual[mask],
            candidate_point[mask],
            champion_point[mask],
            candidate_quantiles[mask],
            champion_quantiles[mask],
            chosen[mask],
            baseline_name="straddle"
            if name == "strict_options"
            else "historical_median",
            **kwargs,
        )
        report["cohorts"][name] = payload
        if payload["status"] != "passed":
            report["status"] = (
                "failed"
                if payload["status"] == "failed" or report["status"] == "failed"
                else "insufficient_data"
            )
            report["issues"].extend(f"{name}: {issue}" for issue in payload["issues"])
    report["evaluation_complete"] = bool(not unsupported and report["evaluation_complete"] and supported_cohorts
                                         and all(row["evaluation_complete"] for row in report["cohorts"].values()))
    report["failure_kind"] = ("quality" if report["evaluation_complete"] and report["status"] == "failed"
                              else "invalid_evidence" if report["status"] == "failed"
                              else "insufficient_data" if report["status"] == "insufficient_data" else None)
    return report


def compare_on_common_holdout(
    candidate_dir: Path,
    champion_dir: Path,
    training_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    max_mae_regression: float = 0.02,
    max_calibration_regression: float = 0.05,
) -> dict[str, Any]:
    """Compare only mutually unexposed prediction snapshots with matching protocols."""
    verify_bundle_dir(candidate_dir)
    verify_bundle_dir(champion_dir)
    report: dict[str, Any] = {
        "status": "passed",
        "method": "mutually_unseen_paired_holdout",
        "horizons": {},
        "issues": [],
    }
    for horizon in horizons:
        cm, hm = _metadata(candidate_dir, horizon), _metadata(champion_dir, horizon)
        ce, he = selection_exposure_end(cm), selection_exposure_end(hm)
        training_metadata_path = training_dir / f"metadata_T{horizon}.json"
        training_metadata = (
            json.loads(training_metadata_path.read_text())
            if training_metadata_path.is_file()
            else {}
        )
        if (
            ce is None
            or he is None
            or feature_protocol(cm) != feature_protocol(hm)
            or target_protocol(cm) != target_protocol(hm)
            or feature_protocol(training_metadata) != feature_protocol(cm)
            or target_protocol(training_metadata) != target_protocol(cm)
        ):
            payload = {
                "status": "insufficient_data",
                "rows": 0,
                "issues": [
                    "exposure or compatible protocol-specific holdout evidence is unavailable"
                ],
            }
        else:
            frame = pd.read_parquet(training_dir / f"training_T{horizon}.parquet")
            dates = pd.to_datetime(frame["__earnings_date"], errors="raise")
            snapshots = (
                pd.to_datetime(frame["__snapshot_date"], errors="raise")
                if "__snapshot_date" in frame
                else dates - pd.Timedelta(days=horizon)
            )
            mask = snapshots > max(ce, he)
            holdout = frame.loc[mask]
            payload = {
                "status": "insufficient_data",
                "rows": len(holdout),
                "issues": [
                    f"unseen paired evidence has only {len(holdout)} rows; require 200"
                ],
            }
            if len(holdout) >= 200:
                holdout.attrs["feature_protocol"] = feature_protocol(cm)
                cp, cq = score_bundle_frame(candidate_dir, horizon, holdout)
                hp, hq = score_bundle_frame(champion_dir, horizon, holdout)
                actual = holdout["target"].to_numpy(dtype=float)
                baseline = pd.to_numeric(
                    holdout["straddle_pct"], errors="coerce"
                ).to_numpy(dtype=float)
                historical = pd.to_numeric(
                    holdout.get(
                        "hist_move_med_4q", pd.Series(np.nan, index=holdout.index)
                    ),
                    errors="coerce",
                ).to_numpy(dtype=float)
                cohorts = holdout.get(
                    "__cohort",
                    pd.Series(
                        np.where(
                            np.isfinite(baseline) & (baseline > 0),
                            "strict_options",
                            "optionless",
                        ),
                        index=holdout.index,
                    ),
                ).to_numpy()
                payload = _cohort_paired_metrics(
                    actual,
                    cp,
                    hp,
                    cq,
                    hq,
                    baseline,
                    historical,
                    cohorts,
                    supported_cohorts=cm.get("supported_cohorts", ["strict_options"]),
                    max_mae_regression=max_mae_regression,
                    max_calibration_regression=max_calibration_regression,
                )
            payload.update(
                excluded_exposed_rows=int((~mask).sum()),
                candidate_exposure_end=ce.date().isoformat(),
                champion_exposure_end=he.date().isoformat(),
            )
        report["horizons"][str(horizon)] = payload
        if payload["status"] != "passed":
            report["status"] = (
                "failed"
                if payload["status"] == "failed" or report["status"] == "failed"
                else "insufficient_data"
            )
            report["issues"].extend(
                f"T-{horizon}: {issue}" for issue in payload["issues"]
            )
    usable = [v for v in report["horizons"].values() if "candidate_mae" in v]
    total = sum(v["rows"] for v in usable)
    report["aggregate"] = {
        "rows": total,
        **{
            k: sum(v[k] * v["rows"] for v in usable if v.get(k) is not None) / total
            if total and all(v.get(k) is not None for v in usable)
            else None
            for k in ["candidate_mae", "champion_mae", "baseline_straddle_mae"]
        },
    }
    report["evaluation_complete"] = bool(report["horizons"] and all(row.get("evaluation_complete") is True for row in report["horizons"].values()))
    report["failure_kind"] = "quality" if report["evaluation_complete"] and report["status"] == "failed" else None
    return report


def prospective_snapshot_mask(frame: pd.DataFrame) -> pd.Series:
    """A snapshot is prospective only while it is the latest completed session."""
    try:
        from market_sessions import latest_completed_us_market_session
    except ModuleNotFoundError:
        from scripts.market_sessions import latest_completed_us_market_session
    result = pd.Series(False, index=frame.index)
    if "recorded_at" not in frame or "snapshot_date" not in frame:
        return result
    recorded = pd.to_datetime(frame["recorded_at"], utc=True, errors="coerce")
    scored = pd.to_datetime(
        frame.get("scored_at", frame["recorded_at"]), utc=True, errors="coerce"
    )
    snapshots = pd.to_datetime(frame["snapshot_date"], errors="coerce").dt.date
    for times in (recorded, scored):
        sessions = {
            stamp: latest_completed_us_market_session(stamp.to_pydatetime())
            for stamp in times.dropna().unique()
        }
        valid = times.map(sessions) == snapshots
        result = valid if times is recorded else result & valid
    return result


def prospective_event_mask(frame: pd.DataFrame) -> pd.Series:
    """Apply the same feature and event deadlines used by publication freezing."""
    try:
        from event_forecast_ledger import audit_forecast_row
    except ModuleNotFoundError:
        from scripts.event_forecast_ledger import audit_forecast_row
    valid = []
    for row in frame.to_dict(orient="records"):
        row["scored_at"] = row.get("scored_at", row.get("recorded_at"))
        audit = audit_forecast_row(row)
        recorded = pd.to_datetime(row.get("recorded_at"), utc=True, errors="coerce")
        scored = pd.to_datetime(row.get("scored_at"), utc=True, errors="coerce")
        deadline = pd.to_datetime(
            audit.get("prediction_deadline_at"), utc=True, errors="coerce"
        )
        valid.append(
            bool(audit["freeze_eligible"] and recorded < deadline and scored < deadline)
        )
    return pd.Series(valid, index=frame.index)


def compare_prospective_outcomes(
    ledger: pd.DataFrame,
    labels: pd.DataFrame,
    candidate_dir: Path,
    champion_dir: Path,
    *,
    champion_id: str,
    candidate_id: str,
    horizon: int,
    min_rows: int = 200,
) -> dict[str, Any]:
    """Evaluate frozen forecasts against the common desired session-reaction target."""
    cm, hm = _metadata(candidate_dir, horizon), _metadata(champion_dir, horizon)
    ce, he = selection_exposure_end(cm), selection_exposure_end(hm)
    empty = {
        "status": "insufficient_data",
        "evaluation_complete": False,
        "failure_kind": "insufficient_data",
        "rows": 0,
        "issues": ["prospective exposure evidence is unavailable"],
        "evaluation_target_protocol": SESSION_TARGET_PROTOCOL,
    }
    if ledger.empty or ce is None or he is None:
        return empty
    required = {"recorded_at", "feature_protocol", "snapshot_date", "bundle_id"}
    if not required.issubset(ledger.columns) or not {
        "__label_available_at",
        "__label_available_date",
    }.intersection(labels.columns):
        return empty
    work = ledger.loc[ledger["model_horizon"] == horizon].copy()
    work["snapshot_date"] = pd.to_datetime(work["snapshot_date"], errors="raise")
    work["earnings_date"] = pd.to_datetime(work["earnings_date"], errors="raise")
    label_column = (
        "__label_available_at"
        if "__label_available_at" in labels
        else "__label_available_date"
    )
    label = labels.rename(
        columns={
            "__symbol": "act_symbol",
            "__earnings_date": "earnings_date",
            label_column: "__label_available_at",
        }
    ).copy()
    label["earnings_date"] = pd.to_datetime(label["earnings_date"], errors="raise")
    work = work.merge(
        label[["act_symbol", "earnings_date", "target", "__label_available_at"]],
        on=["act_symbol", "earnings_date"],
        how="inner",
    )
    recorded = pd.to_datetime(work["recorded_at"], utc=True, errors="coerce")
    available = pd.to_datetime(work["__label_available_at"], utc=True, errors="coerce")
    scored = pd.to_datetime(
        work.get("scored_at", work["recorded_at"]), utc=True, errors="coerce"
    )
    timely = prospective_snapshot_mask(work) & prospective_event_mask(work)
    work = work.loc[
        timely
        & (scored < available)
        & (recorded < available)
        & (available <= pd.Timestamp.now(tz="UTC"))
        & (work["snapshot_date"] > max(ce, he))
    ]
    keys = ["act_symbol", "earnings_date", "model_horizon", "snapshot_date"]
    c = work.loc[
        (work["bundle_id"] == candidate_id)
        & (work["feature_protocol"] == feature_protocol(cm))
    ].drop_duplicates(keys, keep="first")
    h = work.loc[
        (work["bundle_id"] == champion_id)
        & (work["feature_protocol"] == feature_protocol(hm))
    ].drop_duplicates(keys, keep="first")
    common = c.merge(h, on=keys, suffixes=("_candidate", "_champion"), how="inner")
    actual = common["target_candidate"].to_numpy(dtype=float)
    qcols = [f"p{q:02d}" for q in DEFAULT_QUANTILES]
    baseline = pd.to_numeric(common["em_math_pct_candidate"], errors="coerce").to_numpy(
        dtype=float
    )
    historical = pd.to_numeric(
        common.get("hist_move_med_4q_candidate", pd.Series(np.nan, index=common.index)),
        errors="coerce",
    ).to_numpy(dtype=float)
    cohorts = common.get(
        "__cohort_candidate",
        pd.Series(
            np.where(
                np.isfinite(baseline) & (baseline > 0), "strict_options", "optionless"
            ),
            index=common.index,
        ),
    ).to_numpy()
    report = _cohort_paired_metrics(
        actual,
        common["prediction_candidate"].to_numpy(dtype=float),
        common["prediction_champion"].to_numpy(dtype=float),
        common[[q + "_candidate" for q in qcols]].to_numpy(dtype=float),
        common[[q + "_champion" for q in qcols]].to_numpy(dtype=float),
        baseline,
        historical,
        cohorts,
        supported_cohorts=cm.get("supported_cohorts", ["strict_options"]),
        min_rows=min_rows,
    )
    report["evaluation_target_protocol"] = SESSION_TARGET_PROTOCOL
    report["method"] = "prospective_matched_snapshot_predictions"
    return report


def _parse_feature_vectors(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for value in frame["feature_vector"]:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise ValueError("forecast feature_vector is not a JSON object")
        records.append(dict(value))
    vectors = pd.DataFrame(records, index=frame.index).apply(
        pd.to_numeric, errors="coerce"
    )
    if "feature_protocol" in frame and len(frame):
        protocols = set(frame["feature_protocol"].dropna().astype(str))
        if len(protocols) != 1:
            raise ValueError("forecast feature protocols are inconsistent")
        vectors.attrs["feature_protocol"] = protocols.pop()
    return vectors


def _population_stability_index(
    values: pd.Series, reference: Mapping[str, Any]
) -> float | None:
    cuts = np.asarray(reference.get("cuts") or [], dtype=float)
    expected = np.asarray(reference.get("probabilities") or [], dtype=float)
    finite = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if not len(finite) or len(expected) != len(cuts) + 1:
        return None
    actual, _ = np.histogram(finite, bins=np.r_[-np.inf, cuts, np.inf])
    actual = actual / max(1, actual.sum())
    epsilon = 1e-6
    return float(
        np.sum((actual - expected) * np.log((actual + epsilon) / (expected + epsilon)))
    )


def feature_drift_report(
    forecast_frame: pd.DataFrame,
    bundle_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    warning_psi: float = 0.20,
    critical_psi: float = 0.35,
    min_rows: int = 100,
    min_missingness_rows: int = 20,
    references_by_horizon: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    vectors = _parse_feature_vectors(forecast_frame)
    report: dict[str, Any] = {"status": "passed", "horizons": {}}
    total_critical = 0
    total_hard_missing = 0
    total_features = 0
    total_corruption = 0
    sufficient_horizons = 0
    for horizon in horizons:
        rows = forecast_frame["model_horizon"].astype(int) == horizon
        metadata = _metadata(bundle_dir, horizon)
        references = (
            references_by_horizon[horizon]
            if references_by_horizon is not None
            else metadata.get("feature_reference")
        ) or {}
        horizon_rows = int(rows.sum())
        features: dict[str, Any] = {}
        critical = 0
        hard_missing = 0
        warning = 0
        corruption = 0
        sufficient_horizons += int(horizon_rows >= min_rows and bool(references))
        for feature, reference in references.items():
            current = (
                vectors.loc[rows, feature]
                if feature in vectors
                else pd.Series(np.nan, index=vectors.index[rows])
            )
            psi = _population_stability_index(current, reference)
            missing_rate = float(current.isna().mean()) if len(current) else 1.0
            training_missing_rate = float(reference.get("missing_rate", 0.0))
            missing_delta = abs(missing_rate - training_missing_rate)
            is_hard_missing = (
                horizon_rows >= min_missingness_rows
                and missing_rate >= 0.999
                and training_missing_rate <= 0.50
            )
            status = "passed"
            if is_hard_missing:
                status = "critical"
                critical += 1
                hard_missing += 1
                corruption += 1
            elif horizon_rows >= min_missingness_rows and missing_delta >= 0.25:
                status = "critical"
                critical += 1
                corruption += 1
            elif horizon_rows >= min_missingness_rows and missing_delta >= 0.15:
                status = "warning"
                warning += 1
            elif horizon_rows < min_rows:
                status = "low_sample" if horizon_rows else "no_data"
            elif psi is not None and psi >= critical_psi:
                status = "critical"
                critical += 1
            elif psi is not None and psi >= warning_psi:
                status = "warning"
                warning += 1
            features[feature] = {
                "psi": psi,
                "missing_rate": missing_rate,
                "training_missing_rate": training_missing_rate,
                "status": status,
            }
        if horizon_rows >= min_missingness_rows:
            total_features += len(features)
            total_critical += critical
            total_hard_missing += hard_missing
            total_corruption += corruption
        report["horizons"][str(horizon)] = {
            "rows": horizon_rows,
            "critical_features": critical,
            "hard_missing_features": hard_missing,
            "warning_features": warning,
            "features": features,
        }
    critical_limit = max(2, math.ceil(total_features * 0.10))
    if total_hard_missing or total_corruption >= critical_limit:
        report["status"] = "critical"
    elif sufficient_horizons and any(
        row["warning_features"] or row["critical_features"]
        for row in report["horizons"].values()
    ):
        report["status"] = "warning"
    elif not sufficient_horizons:
        report["status"] = "insufficient_data"
    report["corruption_status"] = (
        "critical"
        if total_hard_missing or total_corruption >= critical_limit
        else "passed"
    )
    report["corruption_features"] = total_corruption
    report["covariate_status"] = (
        "insufficient_data"
        if not sufficient_horizons
        else (
            "warning"
            if total_critical
            or any(v["warning_features"] for v in report["horizons"].values())
            else "passed"
        )
    )
    report["critical_features"] = total_critical
    report["hard_missing_features"] = total_hard_missing
    report["critical_limit"] = critical_limit
    return report


def cohort_drift_report(
    forecasts: pd.DataFrame,
    bundle_dir: Path,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """Use the same named training/serving cohorts in promotion and monitoring."""
    baseline = pd.to_numeric(forecasts["em_math_pct"], errors="coerce")
    cohorts = forecasts.get(
        "__cohort",
        pd.Series(
            np.where(
                np.isfinite(baseline) & (baseline > 0), "strict_options", "optionless"
            ),
            index=forecasts.index,
        ),
    )
    result: dict[str, Any] = {
        "status": "passed",
        "horizons": {},
        "critical_features": 0,
        "unsupported_rows": 0,
        "corruption_status": "passed",
    }
    enough = False
    sparse = False
    for horizon in horizons:
        metadata = _metadata(bundle_dir, horizon)
        references = metadata.get("cohort_reference") or {
            "strict_options": {
                "feature_reference": metadata.get("feature_reference") or {}
            }
        }
        mask = forecasts["model_horizon"].astype(int) == horizon
        parts = {}
        for name in ("strict_options", "optionless"):
            selected = forecasts.loc[mask & (cohorts == name)]
            reference = references.get(name)
            if not len(selected):
                parts[name] = {"status": "insufficient_data", "rows": 0}
                continue
            supported = metadata.get("supported_cohorts", ["strict_options"])
            if (
                name not in supported
                or not reference
                or (name == "optionless" and int(reference.get("rows") or 0) < 200)
            ):
                parts[name] = {"status": "unsupported_cohort", "rows": len(selected)}
                result["unsupported_rows"] += len(selected)
                continue
            report = feature_drift_report(
                selected,
                bundle_dir,
                horizons=[horizon],
                references_by_horizon={
                    horizon: reference.get("feature_reference") or {}
                },
            )
            parts[name] = report
            enough |= report["covariate_status"] != "insufficient_data"
            sparse |= report["covariate_status"] == "insufficient_data"
            result["critical_features"] += report["critical_features"]
            if report["corruption_status"] == "critical":
                result["corruption_status"] = "critical"
        result["horizons"][str(horizon)] = {"rows": int(mask.sum()), "cohorts": parts}
    if result["corruption_status"] == "critical":
        result["status"] = "critical"
    elif result["unsupported_rows"]:
        result["status"] = "unsupported_cohort"
    elif not enough or sparse:
        result["status"] = "insufficient_data"
    elif any(
        c.get("status") == "warning"
        for h in result["horizons"].values()
        for c in h["cohorts"].values()
    ):
        result["status"] = "warning"
    return result


def rolling_cohort_drift_report(
    forecasts: pd.DataFrame,
    bundle_dir: Path,
    *,
    bundle_id: str,
    history: pd.DataFrame | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    as_of: Any = None,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """Supplement current drift with exact vectors from caller-verified receipts.

    The caller must authenticate the prior ledger before passing ``history``.
    Current corruption, cohort support, protocol and snapshot checks remain
    independent of the larger sample; old healthy rows cannot hide a bad score.
    """
    now = pd.Timestamp(as_of or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    cutoff = now.normalize() - pd.Timedelta(days=lookback_days)
    current = forecasts.copy().reset_index(drop=True)
    baseline = pd.to_numeric(current["em_math_pct"], errors="coerce")
    derived = pd.Series(np.where(np.isfinite(baseline) & (baseline > 0), "strict_options", "optionless"), index=current.index)
    issues = []
    metadata_by_horizon = {horizon: _metadata(bundle_dir, horizon) for horizon in horizons}

    def valid_vector(value: Any, metadata: dict[str, Any], baseline_value: Any) -> bool:
        try:
            vector = json.loads(value) if isinstance(value, str) else value
            if not isinstance(vector, Mapping) or not set(metadata.get("feature_cols") or []).issubset(vector):
                return False
            if any(value is not None and (not isinstance(value, (int, float)) or not np.isfinite(value))
                   for value in vector.values()):
                return False
            if "log_spot" in (metadata.get("feature_cols") or []) and vector.get("log_spot") is None:
                return False
            if feature_protocol(metadata) != LEGACY_FEATURE_PROTOCOL and (vector.get("timing_bmo"), vector.get("timing_amc")) not in ((1., 0.), (0., 1.)):
                return False
            if "straddle_pct" in vector:
                straddle = vector["straddle_pct"]
                if pd.isna(baseline_value):
                    if straddle is not None:
                        return False
                elif straddle is None or not np.isclose(float(straddle), float(baseline_value)):
                    return False
            return True
        except (TypeError, ValueError):
            return False

    for horizon in horizons:
        metadata = metadata_by_horizon[horizon]
        rows = current.loc[current["model_horizon"] == horizon]
        expected_protocol = feature_protocol(metadata)
        declared = rows.get("feature_protocol", pd.Series(LEGACY_FEATURE_PROTOCOL, index=rows.index))
        targets = rows.get("target_protocol", pd.Series(LEGACY_TARGET_PROTOCOL, index=rows.index))
        if not declared.eq(expected_protocol).all() or not targets.eq(target_protocol(metadata)).all():
            issues.append(f"T-{horizon} current vectors differ from the bundle protocol")
        if not all(valid_vector(row["feature_vector"], metadata, row["em_math_pct"])
                   for row in rows.to_dict(orient="records")):
            issues.append(f"T-{horizon} current feature vector contract is invalid")
        if expected_protocol != LEGACY_FEATURE_PROTOCOL:
            for row in rows.to_dict(orient="records"):
                try:
                    vector = json.loads(row["feature_vector"]) if isinstance(row["feature_vector"], str) else row["feature_vector"]
                except (TypeError, ValueError):
                    # The vector-contract check already records this corruption.
                    continue
                expected = {"bmo": (1., 0.), "amc": (0., 1.)}.get(str(row.get("timing", "")).lower())
                if not isinstance(vector, Mapping) or expected is None or (vector.get("timing_bmo"), vector.get("timing_amc")) != expected:
                    issues.append(f"T-{horizon} current timing differs from its recorded vector")
                    break
    if "model_bundle_id" not in current or not current["model_bundle_id"].eq(bundle_id).all():
        issues.append("current forecast bundle differs from the assessed bundle")
    if "__cohort" in current and not current["__cohort"].eq(derived).all():
        issues.append("current cohort label differs from its recorded baseline")
    current["__cohort"] = derived
    freshness = current.copy()
    freshness["recorded_at"] = now
    scored = pd.to_datetime(freshness.get("scored_at", pd.Series(pd.NaT, index=current.index)), utc=True, errors="coerce")
    fresh = bool(len(current) and (scored <= now).all() and prospective_snapshot_mask(freshness).all())
    if not fresh:
        issues.append("current forecast is not the latest genuinely scored market snapshot")

    eligible = pd.DataFrame()
    required = {"bundle_id", "act_symbol", "earnings_date", "snapshot_date", "model_horizon",
                "feature_protocol", "target_protocol", "__cohort", "em_math_pct", "feature_vector",
                "recorded_at", "scored_at"}
    if history is not None and required.issubset(history):
        prior = history.copy().reset_index(drop=True)
        times = [pd.to_datetime(prior[column], utc=True, errors="coerce")
                 for column in ("recorded_at", "scored_at", "snapshot_date")]
        selected = prior["bundle_id"].eq(bundle_id)
        for dates in times:
            selected &= dates.between(cutoff, now)
        selected &= prospective_snapshot_mask(prior)
        identity = pd.Series(False, index=prior.index)
        for horizon in horizons:
            metadata = metadata_by_horizon[horizon]
            cohorts = set(current.loc[current["model_horizon"] == horizon, "__cohort"])
            identity |= (prior["model_horizon"].eq(horizon)
                         & prior["feature_protocol"].eq(feature_protocol(metadata))
                         & prior["target_protocol"].eq(target_protocol(metadata))
                         & prior["__cohort"].isin(cohorts))
        selected &= identity
        prior = prior.loc[selected].copy()
        verified_vectors = []
        for row in prior.to_dict(orient="records"):
            metadata = metadata_by_horizon[int(row["model_horizon"])]
            value = pd.to_numeric(pd.Series([row["em_math_pct"]]), errors="coerce").iloc[0]
            cohort = "strict_options" if np.isfinite(value) and value > 0 else "optionless"
            verified_vectors.append(cohort == row["__cohort"] and valid_vector(row["feature_vector"], metadata, value))
        eligible = prior.loc[verified_vectors].copy()
    for frame in (current, eligible):
        frame["_historical_drift_row"] = frame is eligible
    combined = pd.concat([current, eligible], ignore_index=True)
    keys = ["act_symbol", "earnings_date", "model_horizon", "snapshot_date"]
    for column in ("earnings_date", "snapshot_date"):
        combined[column] = pd.to_datetime(combined[column], utc=True, errors="coerce").dt.normalize()
    combined = combined.drop_duplicates(keys, keep="first")
    current_report = cohort_drift_report(current, bundle_dir, horizons=horizons) if not issues else {
        "status": "critical", "corruption_status": "critical", "issues": issues,
    }
    report = cohort_drift_report(combined, bundle_dir, horizons=horizons) if not issues else dict(current_report)
    if current_report["corruption_status"] == "critical":
        report["status"] = "critical"
        report["corruption_status"] = "critical"
    elif current_report["status"] == "unsupported_cohort":
        report["status"] = "unsupported_cohort"
    report["current_snapshot"] = current_report
    report["rolling_evidence"] = {
        "method": "signed_observed_feature_vectors", "lookback_days": lookback_days,
        "from": cutoff.isoformat(), "through": now.isoformat(),
        "current_rows": len(current), "historical_rows": int(combined["_historical_drift_row"].sum()),
        "current_snapshot_fresh": fresh,
        "history_available": history is not None,
        "limitation": "prior receipts without exact feature vectors cannot supplement drift",
    }
    return report


def shadow_score_report(
    candidate_forecasts: pd.DataFrame,
    champion_dir: Path,
    *,
    champion_forecasts: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Compare matched predictions; each bundle keeps its own protocol vectors."""
    vectors = _parse_feature_vectors(candidate_forecasts)
    report: dict[str, Any] = {"status": "passed", "horizons": {}, "issues": []}
    horizons = sorted(
        pd.to_numeric(candidate_forecasts["model_horizon"]).astype(int).unique()
    )
    for horizon in horizons:
        mask = candidate_forecasts["model_horizon"].astype(int) == horizon
        candidate = candidate_forecasts.loc[mask]
        if champion_forecasts is None:
            hp, _ = score_bundle_frame(champion_dir, horizon, vectors.loc[mask])
            cp = candidate["em_ml_pct"].to_numpy(dtype=float)
        else:
            # Validate declared champion vectors rather than silently trusting protocol names.
            champion = champion_forecasts.loc[
                champion_forecasts["model_horizon"].astype(int) == horizon
            ]
            marker = _parse_feature_vectors(champion).attrs.get(
                "feature_protocol", "quantiv.earnings-legacy.v1"
            )
            if marker != feature_protocol(_metadata(champion_dir, horizon)):
                raise ValueError(
                    "champion shadow forecast feature protocol differs from bundle"
                )
            keys = ["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]
            paired = candidate.merge(
                champion,
                on=keys,
                suffixes=("_candidate", "_champion"),
                validate="one_to_one",
            )
            cp = paired["em_ml_pct_candidate"].to_numpy(dtype=float)
            hp = paired["em_ml_pct_champion"].to_numpy(dtype=float)
        finite = np.isfinite(cp) & np.isfinite(hp)
        if not finite.any():
            report["status"] = "insufficient_data"
            report["issues"].append(
                f"T-{horizon} has no matched finite shadow predictions"
            )
            continue
        cp, hp = cp[finite], hp[finite]
        difference = np.abs(cp - hp)
        payload = {
            "rows": len(cp),
            "mean_absolute_difference": float(np.mean(difference)),
            "p95_absolute_difference": float(np.quantile(difference, 0.95)),
            "max_absolute_difference": float(np.max(difference)),
            "candidate_mean": float(np.mean(cp)),
            "champion_mean": float(np.mean(hp)),
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
            quantiles = selected[[f"p{q:02d}" for q in DEFAULT_QUANTILES]].to_numpy(
                dtype=float
            )
        else:
            points, quantiles = score_bundle_frame(
                bundle_dir, horizon, vectors.loc[mask]
            )
        output = selected[
            [
                "act_symbol",
                "earnings_date",
                "snapshot_date",
                "model_horizon",
                "em_math_pct",
            ]
        ].copy()
        output["bundle_id"] = bundle_id
        output["feature_vector"] = selected["feature_vector"].values
        output["role"] = role
        metadata = _metadata(bundle_dir, horizon)
        declared = _parse_feature_vectors(selected).attrs.get(
            "feature_protocol", "quantiv.earnings-legacy.v1"
        )
        if declared != feature_protocol(metadata):
            raise ValueError("ledger vectors and model feature protocol differ")
        output["feature_protocol"] = feature_protocol(metadata)
        output["target_protocol"] = target_protocol(metadata)
        baseline = pd.to_numeric(output["em_math_pct"], errors="coerce")
        output["__cohort"] = selected.get(
            "__cohort",
            pd.Series(
                np.where(
                    np.isfinite(baseline) & (baseline > 0),
                    "strict_options",
                    "optionless",
                ),
                index=selected.index,
            ),
        )
        output["hist_move_med_4q"] = vectors.loc[mask].get(
            "hist_move_med_4q", pd.Series(np.nan, index=selected.index)
        )
        output["recorded_at"] = datetime.now(timezone.utc).isoformat()
        output["scored_at"] = (
            selected["scored_at"].values
            if "scored_at" in selected
            else None
        )
        for column in (
            "timing",
            "feature_snapshot_at",
            "call_quote_timestamp",
            "put_quote_timestamp",
            "atm_iv",
        ):
            if column in selected:
                output[column] = selected[column].values
        try:
            from event_forecast_ledger import annotate_forecasts
        except ModuleNotFoundError:
            from scripts.event_forecast_ledger import annotate_forecasts
        annotated = annotate_forecasts(output)
        for column in (
            "feature_cutoff_at",
            "prediction_deadline_at",
            "feature_snapshot_at",
            "freeze_eligible",
            "freeze_ineligible_reason",
        ):
            output[column] = annotated[column].values
        output["prediction"] = points
        for index, quantile in enumerate(DEFAULT_QUANTILES):
            output[f"p{quantile:02d}"] = quantiles[:, index]
        output = output.loc[
            prospective_snapshot_mask(output) & prospective_event_mask(output)
        ]
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
        # Even an empty eligible cohort needs a concrete, signable artifact.
        frames = list(rows[:1])
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
    ledger = ledger.drop_duplicates(keys, keep="first").sort_values(keys)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    ledger.to_parquet(temporary, index=False)
    temporary.replace(ledger_path)
    return ledger


def _outcome_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    actual = frame["target"].to_numpy(dtype=float)
    prediction = frame["prediction"].to_numpy(dtype=float)
    quantiles = frame[[f"p{q:02d}" for q in DEFAULT_QUANTILES]].to_numpy(dtype=float)
    if not (
        np.isfinite(actual).all()
        and np.isfinite(prediction).all()
        and np.isfinite(quantiles).all()
    ):
        raise ValueError(
            "realized outcome metrics require finite targets, predictions, and quantiles"
        )

    baseline = pd.to_numeric(frame["em_math_pct"], errors="coerce").to_numpy(
        dtype=float
    )
    baseline_mask = np.isfinite(baseline)
    baseline_rows = int(baseline_mask.sum())
    baseline_straddle_mae: float | None = None
    baseline_comparable_model_mae: float | None = None
    if baseline_rows:
        baseline_straddle_mae = float(
            mean_absolute_error(actual[baseline_mask], baseline[baseline_mask])
        )
        baseline_comparable_model_mae = float(
            mean_absolute_error(actual[baseline_mask], prediction[baseline_mask])
        )

    residual = actual - prediction
    calibration_error, coverage = _coverage_error(actual, quantiles)
    return {
        "rows": len(frame),
        "mae": float(mean_absolute_error(actual, prediction)),
        "baseline_rows": baseline_rows,
        "optionless_rows": len(frame) - baseline_rows,
        "baseline_comparable_model_mae": baseline_comparable_model_mae,
        "baseline_straddle_mae": baseline_straddle_mae,
        "residual_mean": float(np.mean(residual)),
        "residual_std": float(np.std(residual)),
        "calibration_error": calibration_error,
        **coverage,
    }


def _outcome_slices(frame: pd.DataFrame, *, min_rows: int = 20) -> dict[str, Any]:
    sector = frame.get("__sector", pd.Series("Unknown", index=frame.index)).fillna(
        "Unknown"
    )
    vix = pd.to_numeric(
        frame.get("vix_current", pd.Series(np.nan, index=frame.index)), errors="coerce"
    )
    volatility = pd.Series(
        np.select(
            [vix < 15, vix.between(15, 25, inclusive="left"), vix >= 25],
            ["low VIX (<15)", "normal VIX (15–25)", "high VIX (≥25)"],
            default="VIX unavailable",
        ),
        index=frame.index,
    )
    volume = pd.to_numeric(
        frame.get("__dollar_volume", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    liquidity = pd.Series(
        np.select(
            [
                volume < 20_000_000,
                volume.between(20_000_000, 100_000_000, inclusive="left"),
                volume >= 100_000_000,
            ],
            ["lower", "medium", "higher"],
            default="unavailable",
        ),
        index=frame.index,
    )
    dte = pd.to_numeric(
        frame.get("dte", pd.Series(np.nan, index=frame.index)), errors="coerce"
    )
    dte_bucket = pd.Series(
        np.select(
            [
                dte <= 3,
                dte.between(4, 7),
                dte.between(8, 14),
                dte.between(15, 30),
                dte > 30,
            ],
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
        frame = pd.read_parquet(
            path, columns=[column for column in columns if column in available]
        )
        frame["model_horizon"] = horizon
        frame = frame.drop_duplicates(["__symbol", "__earnings_date"], keep="last")
        realized_parts.append(frame)
    realized = pd.concat(realized_parts, ignore_index=True).rename(
        columns={"__symbol": "act_symbol", "__earnings_date": "earnings_date"}
    )
    realized["earnings_date"] = pd.to_datetime(
        realized["earnings_date"], errors="raise"
    )
    work = ledger.copy()
    work["earnings_date"] = pd.to_datetime(work["earnings_date"], errors="raise")
    work = work.merge(
        realized, on=["act_symbol", "earnings_date", "model_horizon"], how="inner"
    )
    work = work.loc[work["snapshot_date"] < work["earnings_date"]]
    work = work.sort_values("snapshot_date").drop_duplicates(
        ["bundle_id", "act_symbol", "earnings_date", "model_horizon", "snapshot_date"],
        keep="first",
    )
    champion = work.loc[work["bundle_id"] == champion_id]
    comparison = work.loc[work["bundle_id"] == comparison_id]
    common_keys = ["act_symbol", "earnings_date", "model_horizon", "snapshot_date"]
    common = (
        champion[common_keys]
        .merge(comparison[common_keys], on=common_keys)
        .drop_duplicates()
    )
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
            abs(observed["residual_mean"] - float(reference.get("mean") or 0.0))
            > 2 * reference_std
            or observed["residual_std"] > 1.75 * reference_std
        ):
            residual_alerts.append(
                {"horizon": horizon, "observed": observed, "reference": reference}
            )
    report["residual_drift_alerts"] = residual_alerts
    baseline_model_mae = champion_metrics["baseline_comparable_model_mae"]
    baseline_straddle_mae = champion_metrics["baseline_straddle_mae"]
    champion_worse_than_market = bool(
        baseline_model_mae is not None
        and baseline_straddle_mae is not None
        and baseline_model_mae > baseline_straddle_mae * 1.05
    )
    comparison_materially_better = (
        comparison_metrics["mae"] < champion_metrics["mae"] * 0.95
    )
    severe_undercoverage = champion_metrics["coverage_80"] < 0.65
    report["rollback_recommended"] = bool(
        comparison_materially_better
        and (champion_worse_than_market or severe_undercoverage)
    )
    report["rollback_reasons"] = {
        "champion_worse_than_market": champion_worse_than_market,
        "comparison_materially_better": comparison_materially_better,
        "severe_80pct_undercoverage": severe_undercoverage,
    }
    _ = comparison_dir  # verified by the caller; retained for an explicit audit signature.
    return report


def update_outcome_history(
    existing: Mapping[str, Any] | None,
    report: Mapping[str, Any],
    *,
    limit: int = 52,
) -> dict[str, Any]:
    """Retain bounded weekly outcome evidence without a database dependency."""
    if limit < 1:
        raise ValueError("outcome history limit must be at least 1")
    evaluated_at = str(report.get("evaluated_at") or "")
    entry = {
        key: value
        for key, value in {
            "evaluated_at": evaluated_at,
            "status": report.get("status", "unavailable"),
            "common_rows": int(report.get("common_rows") or 0),
            "minimum_common_rows": int(report.get("minimum_common_rows") or 0),
            "champion_bundle_id": report.get("champion_bundle_id"),
            "comparison_bundle_id": report.get("comparison_bundle_id"),
            "champion": report.get("champion"),
            "comparison": report.get("comparison"),
            "residual_drift_alerts": len(report.get("residual_drift_alerts") or []),
            "rollback_recommended": bool(report.get("rollback_recommended")),
            "rolled_back": bool(report.get("rolled_back")),
            "rollback_reasons": report.get("rollback_reasons"),
            "reason": report.get("reason"),
        }.items()
        if value is not None
    }
    prior = [
        dict(item)
        for item in ((existing or {}).get("evaluations") or [])
        if isinstance(item, Mapping)
        and str(item.get("evaluated_at") or "") != evaluated_at
    ]
    return {
        "schema": "quantiv.model-outcome-history.v1",
        "updated_at": evaluated_at,
        "evaluations": [entry, *prior][:limit],
    }


__all__ = [
    "compare_on_common_holdout",
    "compare_prospective_outcomes",
    "cohort_drift_report",
    "rolling_cohort_drift_report",
    "prospective_snapshot_mask",
    "append_prediction_ledger",
    "evaluate_realized_outcomes",
    "feature_drift_report",
    "score_bundle_frame",
    "shadow_score_report",
    "monitoring_rows",
    "update_outcome_history",
]
