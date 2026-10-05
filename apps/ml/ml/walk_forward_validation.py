"""Mandatory, purged walk-forward publication gate for candidate models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import ceil
from typing import Any, Sequence

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation
from sklearn.metrics import mean_absolute_error

from ml.training_split import chronological_train_val_split, half_life_weights, temporal_dates
from ml.model_protocol import CAUSAL_FEATURE_PROTOCOL, feature_protocol

DEFAULT_PARAMS: dict[str, Any] = {
    "learning_rate": 0.03,
    "max_depth": 7,
    "num_leaves": 63,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "reg_alpha": 0.05,
    "reg_lambda": 0.1,
}


@dataclass(frozen=True)
class WalkForwardFold:
    validation_start: str
    validation_end: str
    train_end: str
    rows_train: int
    rows_validation: int
    model_mae: float
    baseline_straddle_mae: float
    model_to_baseline_ratio: float
    validation_snapshot_start: str | None = None
    label_available_end: str | None = None
    selected_iterations: int | None = None
    refit_iterations: int | None = None
    cohorts: dict[str, Any] | None = None


def _fold_windows(dates: pd.Series, *, folds: int, test_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    end = pd.Timestamp(dates.max()).normalize() + pd.Timedelta(days=1)
    return [
        (
            end - pd.Timedelta(days=test_days * (folds - index)),
            end - pd.Timedelta(days=test_days * (folds - index - 1)),
        )
        for index in range(folds)
    ]


def _time_decay_weights(dates: pd.Series, cutoff: pd.Timestamp, half_life_years: float) -> np.ndarray | None:
    return half_life_weights(dates, cutoff, half_life_years)


def assess_walk_forward_folds(
    folds: Sequence[WalkForwardFold],
    *,
    min_folds: int = 3,
    max_worst_fold_ratio: float = 1.50,
) -> dict[str, Any]:
    rows = list(folds)
    total_rows = sum(row.rows_validation for row in rows)
    if total_rows:
        model_mae = sum(row.model_mae * row.rows_validation for row in rows) / total_rows
        baseline_mae = (
            sum(row.baseline_straddle_mae * row.rows_validation for row in rows) / total_rows
        )
    else:
        model_mae = float("nan")
        baseline_mae = float("nan")
    folds_beating = sum(row.model_mae < row.baseline_straddle_mae for row in rows)
    worst_ratio = max((row.model_to_baseline_ratio for row in rows), default=float("inf"))
    issues: list[str] = []
    if len(rows) < min_folds:
        issues.append(f"only {len(rows)} usable folds; require at least {min_folds}")
    if not np.isfinite(model_mae) or not np.isfinite(baseline_mae) or model_mae >= baseline_mae:
        issues.append("aggregate walk-forward MAE does not beat the matched cohort baseline")
    if rows and folds_beating < ceil(len(rows) / 2):
        issues.append("candidate fails to beat the matched cohort baseline in at least half of folds")
    if worst_ratio > max_worst_fold_ratio:
        issues.append(
            f"worst-fold model/baseline MAE ratio {worst_ratio:.3f} exceeds {max_worst_fold_ratio:.3f}"
        )
    return {
        "status": "passed" if not issues else "failed",
        "issues": issues,
        "fold_count": len(rows),
        "validation_rows": total_rows,
        "model_mae": model_mae,
        "baseline_straddle_mae": baseline_mae,
        "baseline_matched_mae": baseline_mae,
        "baseline_name": "cohort_matched_straddle_or_historical_median",
        "improvement_vs_straddle": (
            float(1 - model_mae / baseline_mae) if baseline_mae > 0 else None
        ),
        "improvement_vs_matched_baseline": (
            float(1 - model_mae / baseline_mae) if baseline_mae > 0 else None
        ),
        "folds_beating_baseline": folds_beating,
        "worst_fold_ratio": worst_ratio,
        "folds": [asdict(row) for row in rows],
    }


def assess_cohort_walk_forward_folds(
    folds: Sequence[WalkForwardFold], *, supported_cohorts: Sequence[str],
    min_validation_rows: int = 30, min_folds: int = 3,
    max_worst_fold_ratio: float = 1.50,
) -> dict[str, Any]:
    reports = {}
    for name in supported_cohorts:
        usable = []
        for fold in folds:
            evidence = (fold.cohorts or {}).get(name, {})
            if int(evidence.get("rows", 0)) < min_validation_rows:
                continue
            model = float(evidence["model_mae"])
            baseline = float(evidence["baseline_mae"])
            usable.append(replace(fold, rows_validation=int(evidence["rows"]),
                                  model_mae=model, baseline_straddle_mae=baseline,
                                  model_to_baseline_ratio=model/baseline if baseline else float("inf")))
        reports[name] = assess_walk_forward_folds(usable, min_folds=min_folds,
                                                  max_worst_fold_ratio=max_worst_fold_ratio)
    return reports


def assess_serving_walk_forward_folds(
    folds: Sequence[WalkForwardFold], metadata: dict[str, Any], *,
    min_validation_rows: int = 30, min_folds: int = 3,
    max_worst_fold_ratio: float = 1.50,
) -> dict[str, Any]:
    """Withheld cohorts remain diagnostic and cannot veto a supported cohort."""
    kwargs = {"min_folds": min_folds, "max_worst_fold_ratio": max_worst_fold_ratio}
    all_source = assess_walk_forward_folds(folds, **kwargs)
    declared = metadata.get("supported_cohorts")
    supported = list(declared) if declared is not None else [
        name for name in ("strict_options", "optionless")
        if any((row.cohorts or {}).get(name, {}).get("rows", 0) for row in folds)
    ]
    causal = feature_protocol(metadata) == CAUSAL_FEATURE_PROTOCOL
    scoped = []
    if causal:
        for fold in folds:
            cohorts = [(fold.cohorts or {}).get(name, {}) for name in supported]
            selected = [row for row in cohorts if int(row.get("rows", 0)) > 0]
            count = sum(int(row["rows"]) for row in selected)
            if count < min_validation_rows:
                continue
            model = sum(float(row["model_mae"]) * int(row["rows"]) for row in selected) / count
            baseline = sum(float(row["baseline_mae"]) * int(row["rows"]) for row in selected) / count
            scoped.append(replace(fold, rows_validation=count, model_mae=model,
                                  baseline_straddle_mae=baseline,
                                  model_to_baseline_ratio=model / baseline if baseline else float("inf")))
    else:
        scoped = list(folds)
    assessment = assess_walk_forward_folds(scoped, **kwargs)
    cohort_assessments = assess_cohort_walk_forward_folds(
        folds, supported_cohorts=supported, min_validation_rows=min_validation_rows,
        **kwargs,
    )
    for name, result in cohort_assessments.items():
        if result["status"] != "passed":
            assessment["status"] = "failed"
            assessment["issues"].extend(f"{name}: {issue}" for issue in result["issues"])
    return {**assessment, "cohort_assessments": cohort_assessments,
            "assessment_scope": "supported_serving_cohorts" if causal else "all_source_legacy",
            "all_source_diagnostics": all_source}


def run_walk_forward_gate(
    frame: pd.DataFrame,
    metadata: dict[str, Any],
    *,
    folds: int = 4,
    test_days: int = 60,
    purge_days: int = 5,
    min_train_rows: int = 1_000,
    min_validation_rows: int = 30,
    max_worst_fold_ratio: float = 1.50,
) -> dict[str, Any]:
    feature_cols = list(metadata.get("feature_cols") or [])
    required = {"target", "__earnings_date", "straddle_pct", *feature_cols}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"walk-forward artifact is missing columns: {missing}")
    work = frame.copy()
    work["__earnings_date"] = pd.to_datetime(work["__earnings_date"], errors="raise")
    work = work.sort_values(["__earnings_date", "__symbol"], kind="mergesort")

    params = dict(DEFAULT_PARAMS)
    params.update(
        {
            "objective": "regression_l1",
            "n_estimators": 500,
            "random_state": 42,
            "verbose": -1,
            "n_jobs": -1,
        }
    )
    half_life = float(metadata.get("time_decay_years") or 0.0)
    horizon = int(metadata.get("horizon") or 0)
    snapshots, labels, availability_basis = temporal_dates(
        work, horizon_days=horizon, purge_days=purge_days,
    )
    results: list[WalkForwardFold] = []
    for validation_start, validation_end in _fold_windows(
        work["__earnings_date"], folds=folds, test_days=test_days
    ):
        validation = work.loc[
            (work["__earnings_date"] >= validation_start)
            & (work["__earnings_date"] < validation_end)
        ]
        if validation.empty:
            continue
        prediction_cutoff = snapshots.loc[validation.index].min()
        training = work.loc[(work["__earnings_date"] < validation_start) & (labels < prediction_cutoff)]
        if len(training) < min_train_rows or len(validation) < min_validation_rows:
            continue
        X_train = training[feature_cols].replace([np.inf, -np.inf], np.nan)
        X_validation = validation[feature_cols].replace([np.inf, -np.inf], np.nan)
        y_train = training["target"].to_numpy(dtype=float)
        y_validation = validation["target"].to_numpy(dtype=float)
        # Each fold selects its tree count only inside that fold's training
        # history. Candidate-wide tuning metadata has seen later labels.
        try:
            inner_train, inner_val, _ = chronological_train_val_split(
                training, horizon_days=horizon, purge_days=purge_days,
            )
        except ValueError:
            continue
        fold_params = {**params, "min_child_samples": max(10, len(inner_train) // 50)}
        selection_model = LGBMRegressor(**fold_params)
        selection_model.fit(
            inner_train[feature_cols], inner_train["target"],
            sample_weight=_time_decay_weights(inner_train["__earnings_date"], inner_train["__earnings_date"].max(), half_life),
            eval_set=[(inner_val[feature_cols], inner_val["target"])],
            callbacks=[early_stopping(50, verbose=False), log_evaluation(0)],
        )
        selected_iterations = int(selection_model.booster_.num_trees())
        fold_params["n_estimators"] = selected_iterations
        weights = _time_decay_weights(training["__earnings_date"], training["__earnings_date"].max(), half_life)
        model = LGBMRegressor(**fold_params)
        model.fit(X_train, y_train, sample_weight=weights)
        predictions = np.clip(model.predict(X_validation), 0.0, None)
        baseline = pd.to_numeric(validation["straddle_pct"], errors="coerce")
        strict_options = np.isfinite(baseline.to_numpy(dtype=float)) & (baseline.to_numpy(dtype=float) > 0)
        historical = pd.to_numeric(validation.get("hist_move_med_4q", pd.Series(np.nan, index=validation.index)), errors="coerce")
        baseline = baseline.where(np.isfinite(baseline) & (baseline > 0), historical)
        matched = np.isfinite(baseline.to_numpy(dtype=float)) & (baseline.to_numpy(dtype=float) >= 0)
        if int(matched.sum()) < min_validation_rows:
            continue
        model_mae = float(mean_absolute_error(y_validation[matched], predictions[matched]))
        baseline_mae = float(mean_absolute_error(y_validation[matched], baseline.to_numpy(dtype=float)[matched]))
        cohort_reports = {}
        for name, cohort in (("strict_options", strict_options), ("optionless", ~strict_options)):
            selected = cohort & matched
            count = int(selected.sum())
            cohort_reports[name] = {
                "rows": count, "cohort_rows": int(cohort.sum()),
                "model_mae": float(mean_absolute_error(y_validation[selected], predictions[selected])) if count else None,
                "baseline_mae": float(mean_absolute_error(y_validation[selected], baseline.to_numpy(dtype=float)[selected])) if count else None,
                "baseline_name": "straddle" if name == "strict_options" else "historical_median",
                "status": "reported" if count >= min_validation_rows else "low_sample",
            }
        results.append(
            WalkForwardFold(
                validation_start=validation_start.date().isoformat(),
                validation_end=(validation_end - pd.Timedelta(days=1)).date().isoformat(),
                train_end=training["__earnings_date"].max().date().isoformat(),
                rows_train=len(training),
                rows_validation=int(matched.sum()),
                model_mae=model_mae,
                baseline_straddle_mae=baseline_mae,
                model_to_baseline_ratio=(model_mae / baseline_mae if baseline_mae else float("inf")),
                validation_snapshot_start=prediction_cutoff.date().isoformat(),
                label_available_end=labels.loc[training.index].max().date().isoformat(),
                selected_iterations=selected_iterations,
                refit_iterations=int(model.booster_.num_trees()),
                cohorts=cohort_reports,
            )
        )
    assessment = assess_serving_walk_forward_folds(
        results, metadata, min_validation_rows=min_validation_rows,
        min_folds=min(3, folds), max_worst_fold_ratio=max_worst_fold_ratio,
    )
    return {
        "method": "expanding_purged_walk_forward",
        "purge_days": purge_days,
        "test_days": test_days,
        "requested_folds": folds,
        "recipe_source": "fold_training_only",
        "availability_basis": availability_basis,
        "metric_scope": "fixed_recipe_training_procedure_walk_forward",
        "candidate_tuning_replayed": False,
        **assessment,
    }


__all__ = [
    "WalkForwardFold",
    "assess_walk_forward_folds",
    "assess_cohort_walk_forward_folds",
    "assess_serving_walk_forward_folds",
    "run_walk_forward_gate",
]
