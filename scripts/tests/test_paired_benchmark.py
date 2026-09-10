import numpy as np
import pandas as pd
import pytest

from research.paired_benchmark import compare_forecasts


def test_model_beats_baseline_with_confidence_interval_below_zero() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "model": [1.1, 1.9, 3.1, 3.9, 5.1, 5.9],
            "baseline": [1.8, 1.2, 3.8, 3.2, 5.8, 5.2],
        }
    )

    report = compare_forecasts(
        frame,
        actual_column="actual",
        model_column="model",
        baseline_column="baseline",
        draws=1_000,
        seed=5,
    )

    overall = report["overall"]
    assert overall["model_mae"] < overall["baseline_mae"]
    assert overall["mean_absolute_error_difference"] < 0
    assert overall["mean_absolute_error_difference_95_ci"][1] < 0
    assert overall["model_win_rate"] == 1.0
    assert overall["bootstrap_unit"] == "rows"


def test_group_report_respects_minimum_sample_size() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0, 3.0, 4.0, 5.0],
            "model": [1.0, 2.1, 3.1, 4.1, 5.1],
            "baseline": [1.4, 2.4, 3.4, 4.4, 5.4],
            "sector": ["A", "A", "A", "B", "B"],
        }
    )

    report = compare_forecasts(
        frame,
        actual_column="actual",
        model_column="model",
        baseline_column="baseline",
        group_column="sector",
        min_group_size=3,
        draws=100,
        seed=3,
    )

    assert set(report["groups"]) == {"A"}
    assert report["groups"]["A"]["n"] == 3


def test_cluster_bootstrap_is_reproducible_and_resamples_complete_clusters() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 1.2, 2.0, 2.3, 3.0, 3.1],
            "model": [1.1, 1.1, 2.1, 2.2, 3.1, 3.0],
            "baseline": [1.5, 1.6, 2.5, 2.7, 3.5, 3.6],
            "issuer": ["A", "A", "B", "B", "C", "C"],
        }
    )

    first = compare_forecasts(
        frame,
        actual_column="actual",
        model_column="model",
        baseline_column="baseline",
        cluster_column="issuer",
        draws=500,
        seed=11,
    )
    second = compare_forecasts(
        frame,
        actual_column="actual",
        model_column="model",
        baseline_column="baseline",
        cluster_column="issuer",
        draws=500,
        seed=11,
    )

    assert first == second
    assert first["cluster_column"] == "issuer"
    assert first["overall"]["bootstrap_unit"] == "cluster:issuer"
    assert first["overall"]["clusters"] == 3


def test_cluster_bootstrap_requires_multiple_clusters() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0, 3.0],
            "model": [1.1, 2.1, 3.1],
            "baseline": [1.3, 2.3, 3.3],
            "issuer": ["A", "A", "A"],
        }
    )

    with pytest.raises(ValueError, match="at least two clusters"):
        compare_forecasts(
            frame,
            actual_column="actual",
            model_column="model",
            baseline_column="baseline",
            cluster_column="issuer",
            draws=10,
        )


def test_non_finite_values_fail_closed() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0],
            "model": [1.1, np.inf],
            "baseline": [1.2, 2.2],
        }
    )

    with pytest.raises(ValueError, match="non-finite"):
        compare_forecasts(
            frame,
            actual_column="actual",
            model_column="model",
            baseline_column="baseline",
            draws=10,
        )
