from __future__ import annotations

from ml.walk_forward_validation import WalkForwardFold, assess_walk_forward_folds


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
