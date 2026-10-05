from __future__ import annotations

from ml.walk_forward_validation import WalkForwardFold, assess_walk_forward_folds
import numpy as np
import pandas as pd
import pytest

from ml.walk_forward_validation import run_walk_forward_gate, _time_decay_weights
from ml.walk_forward_validation import assess_cohort_walk_forward_folds


def _fold(model: float, baseline: float) -> WalkForwardFold:
    return WalkForwardFold(
        validation_start="2026-01-01",
        validation_end="2026-02-28",
        train_end="2025-12-26",
        rows_train=2_000,
        rows_validation=100,
        model_mae=model,
        baseline_straddle_mae=baseline,
        model_to_baseline_ratio=model / baseline,
    )


def test_walk_forward_gate_requires_aggregate_and_fold_level_baseline_quality() -> None:
    passed = assess_walk_forward_folds(
        [_fold(0.04, 0.05), _fold(0.045, 0.05), _fold(0.048, 0.05)]
    )
    assert passed["status"] == "passed"
    assert passed["folds_beating_baseline"] == 3

    failed = assess_walk_forward_folds(
        [_fold(0.04, 0.05), _fold(0.055, 0.05), _fold(0.09, 0.05)]
    )
    assert failed["status"] == "failed"
    assert any("worst-fold" in issue for issue in failed["issues"])


def test_walk_forward_gate_rejects_too_few_folds() -> None:
    result = assess_walk_forward_folds([_fold(0.04, 0.05)], min_folds=3)
    assert result["status"] == "failed"
    assert any("usable folds" in issue for issue in result["issues"])


def test_good_straddle_results_cannot_mask_losing_optionless_folds():
    from dataclasses import replace

    folds = [replace(_fold(.04, .05), cohorts={
        "strict_options": {"rows": 200, "model_mae": .02, "baseline_mae": .05},
        "optionless": {"rows": 50, "model_mae": .09, "baseline_mae": .05},
    }) for _ in range(4)]
    result = assess_cohort_walk_forward_folds(folds, supported_cohorts=["strict_options", "optionless"])
    assert result["strict_options"]["status"] == "passed"
    assert result["optionless"]["status"] == "failed"
    undersampled = [replace(fold, cohorts={"optionless": {"rows": 2, "model_mae": .02, "baseline_mae": .05}}) for fold in folds]
    assert assess_cohort_walk_forward_folds(undersampled, supported_cohorts=["optionless"])["optionless"]["status"] == "failed"


def test_walk_forward_folds_select_recipes_without_future_tuning_metadata():
    dates = pd.date_range("2025-01-01", periods=300)
    frame = pd.DataFrame({
        "feature": np.arange(300, dtype=float), "straddle_pct": [.08] * 300,
        "target": .04 + .01 * np.sin(np.arange(300) / 11), "__symbol": [f"S{i}" for i in range(300)],
        "__earnings_date": dates, "__snapshot_date": dates - pd.Timedelta(days=21),
        "__label_available_at": dates + pd.Timedelta(days=1),
    })
    metadata = {"horizon": 21, "feature_cols": ["feature", "straddle_pct"]}
    first = run_walk_forward_gate(frame, metadata, folds=3, test_days=20,
                                 min_train_rows=50, min_validation_rows=10)
    metadata.update({"best_params": {"learning_rate": .99, "num_leaves": 100},
                     "best_iteration": 777})
    changed = run_walk_forward_gate(frame, metadata, folds=3, test_days=20,
                                   min_train_rows=50, min_validation_rows=10)
    assert first["folds"] == changed["folds"]
    assert first["recipe_source"] == "fold_training_only"
    assert first["baseline_name"] == "cohort_matched_straddle_or_historical_median"
    for fold in first["folds"]:
        assert pd.Timestamp(fold["label_available_end"]) < pd.Timestamp(fold["validation_snapshot_start"])
        assert fold["selected_iterations"] >= 1
        assert fold["refit_iterations"] == fold["selected_iterations"]
        assert fold["cohorts"]["strict_options"]["rows"] == 20


def test_walk_forward_time_weight_is_half_at_one_half_life():
    dates = pd.Series(pd.to_datetime(["2024-01-02", "2025-01-01"]))
    assert _time_decay_weights(dates, pd.Timestamp("2025-01-01"), 1.).tolist() == pytest.approx([.5, 1.])


def test_causal_walk_forward_only_gates_declared_serving_cohorts():
    from dataclasses import replace
    from ml.walk_forward_validation import assess_serving_walk_forward_folds

    folds = [replace(_fold(.09, .05), cohorts={
        "strict_options": {"rows": 200, "model_mae": .10, "baseline_mae": .01},
        "optionless": {"rows": 50, "model_mae": .02, "baseline_mae": .05},
    }) for _ in range(4)]
    metadata = {"feature_protocol": "quantiv.earnings-causal.v2", "supported_cohorts": ["optionless"]}
    causal = assess_serving_walk_forward_folds(folds, metadata)
    assert causal["status"] == "passed"
    assert causal["validation_rows"] == 200
    assert causal["assessment_scope"] == "supported_serving_cohorts"
    assert causal["all_source_diagnostics"]["status"] == "failed"
    legacy = assess_serving_walk_forward_folds(folds, {"supported_cohorts": ["optionless"]})
    assert legacy["status"] == "failed"
    assert legacy["assessment_scope"] == "all_source_legacy"
    metadata["supported_cohorts"] = ["strict_options", "optionless"]
    assert assess_serving_walk_forward_folds(folds, metadata)["status"] == "failed"
