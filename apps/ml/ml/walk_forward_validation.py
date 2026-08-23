"""Mandatory, purged walk-forward publication gate for candidate models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from typing import Any, Sequence

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

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
    if half_life_years <= 0:
        return None
    days_old = (cutoff - pd.to_datetime(dates)).dt.days.clip(lower=0).to_numpy(dtype=float)
    return np.exp(-days_old / (365.0 * half_life_years))


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
        issues.append("aggregate walk-forward MAE does not beat the straddle baseline")
    if rows and folds_beating < ceil(len(rows) / 2):
        issues.append("candidate fails to beat the straddle baseline in at least half of folds")
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
        "improvement_vs_straddle": (
            float(1 - model_mae / baseline_mae) if baseline_mae > 0 else None
        ),
        "folds_beating_baseline": folds_beating,
        "worst_fold_ratio": worst_ratio,
        "folds": [asdict(row) for row in rows],
    }


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
    params.update(metadata.get("best_params") or {})
    params.update(
        {
            "objective": "regression_l1",
            "n_estimators": max(100, min(int(metadata.get("best_iteration") or 500), 1_500)),
            "random_state": 42,
            "verbose": -1,
            "n_jobs": -1,
        }
    )
    half_life = float(metadata.get("time_decay_years") or 0.0)
    results: list[WalkForwardFold] = []
    for validation_start, validation_end in _fold_windows(
        work["__earnings_date"], folds=folds, test_days=test_days
    ):
        train_end_exclusive = validation_start - pd.Timedelta(days=purge_days)
        training = work.loc[work["__earnings_date"] < train_end_exclusive]
        validation = work.loc[
            (work["__earnings_date"] >= validation_start)
            & (work["__earnings_date"] < validation_end)
        ]
        if len(training) < min_train_rows or len(validation) < min_validation_rows:
            continue
        X_train = training[feature_cols].replace([np.inf, -np.inf], np.nan)
        X_validation = validation[feature_cols].replace([np.inf, -np.inf], np.nan)
        y_train = training["target"].to_numpy(dtype=float)
        y_validation = validation["target"].to_numpy(dtype=float)
        weights = _time_decay_weights(
            training["__earnings_date"], train_end_exclusive, half_life
        )
        model = LGBMRegressor(**params)
        model.fit(X_train, y_train, sample_weight=weights)
        predictions = np.clip(model.predict(X_validation), 0.0, None)
        baseline = pd.to_numeric(
            validation["straddle_pct"], errors="coerce"
        ).fillna(float(np.mean(y_train)))
        model_mae = float(mean_absolute_error(y_validation, predictions))
        baseline_mae = float(mean_absolute_error(y_validation, baseline))
        results.append(
            WalkForwardFold(
                validation_start=validation_start.date().isoformat(),
                validation_end=(validation_end - pd.Timedelta(days=1)).date().isoformat(),
                train_end=(train_end_exclusive - pd.Timedelta(days=1)).date().isoformat(),
                rows_train=len(training),
                rows_validation=len(validation),
                model_mae=model_mae,
                baseline_straddle_mae=baseline_mae,
                model_to_baseline_ratio=(model_mae / baseline_mae if baseline_mae else float("inf")),
            )
        )
    assessment = assess_walk_forward_folds(
        results,
        min_folds=min(3, folds),
        max_worst_fold_ratio=max_worst_fold_ratio,
    )
    return {
        "method": "expanding_purged_walk_forward",
        "purge_days": purge_days,
        "test_days": test_days,
        "requested_folds": folds,
        **assessment,
    }


__all__ = [
    "WalkForwardFold",
    "assess_walk_forward_folds",
    "run_walk_forward_gate",
]
