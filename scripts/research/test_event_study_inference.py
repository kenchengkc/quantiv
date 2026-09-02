import numpy as np

from scripts.research.event_study_inference import (
    moving_block_bootstrap_mean,
    sign_flip_pvalue,
    summarize_event_study,
)


def test_summary_reports_positive_excess_and_tight_ci_for_clear_effect() -> None:
    priced = np.full(24, 0.04)
    realized = np.array([0.055, 0.061, 0.052, 0.058, 0.063, 0.056] * 4)

    report = summarize_event_study(
        realized,
        priced,
        block_size=3,
        bootstrap_draws=1_500,
        permutation_draws=2_500,
        seed=17,
    )

    assert report["n"] == 24
    assert report["mean_excess_move"] > 0.01
    assert report["bootstrap_95_ci_mean_excess"][0] > 0
    assert report["sign_flip_pvalue"] < 0.01
    assert report["realized_exceeded_priced_rate"] == 1.0


def test_sign_flip_null_is_not_significant_for_balanced_sample() -> None:
    values = np.array([-0.03, -0.02, -0.01, 0.01, 0.02, 0.03])
    assert sign_flip_pvalue(values, draws=2_000, seed=3) > 0.5


def test_moving_block_bootstrap_is_reproducible() -> None:
    values = np.arange(1.0, 9.0)
    first = moving_block_bootstrap_mean(values, block_size=2, draws=50, seed=23)
    second = moving_block_bootstrap_mean(values, block_size=2, draws=50, seed=23)
    np.testing.assert_array_equal(first, second)
