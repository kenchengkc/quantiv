import numpy as np
import pandas as pd
import pytest

from research.quantile_calibration import (
    evaluate_quantiles,
    parse_quantile_columns,
    pinball_loss,
)


def test_calibration_report_tracks_coverage_intervals_and_crossing() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0, 3.0, 4.0],
            "q10": [0.5, 1.5, 2.5, 3.5],
            "q25": [0.8, 1.8, 2.8, 3.8],
            "q50": [1.0, 2.0, 3.0, 4.0],
            "q75": [1.2, 2.2, 3.2, 4.2],
            "q90": [1.5, 2.5, 3.5, 4.5],
        }
    )

    report = evaluate_quantiles(frame, target_column="actual")

    assert report["n"] == 4
    assert report["quantiles"]["q50"]["empirical"] == 1.0
    assert report["quantile_crossing_rate"] == 0.0
    assert report["intervals"]["q10_q90"]["empirical_coverage"] == 1.0
    assert report["intervals"]["q25_q75"]["mean_width"] == pytest.approx(0.4)


def test_calibration_report_detects_crossed_quantiles() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0],
            "q10": [0.5, 1.5],
            "q25": [0.8, 1.8],
            "q50": [1.0, 2.0],
            "q75": [1.2, 1.9],
            "q90": [1.5, 1.7],
        }
    )

    report = evaluate_quantiles(frame, target_column="actual")
    assert report["quantile_crossing_rate"] == 0.5


def test_pinball_loss_is_zero_for_exact_forecast() -> None:
    actual = pd.Series([1.0, 2.0, 3.0]).to_numpy()
    assert pinball_loss(actual, actual.copy(), 0.5) == 0.0


def test_custom_quantile_mapping_validation() -> None:
    assert parse_quantile_columns(["p20=0.2", "p80=0.8"]) == {
        "p20": 0.2,
        "p80": 0.8,
    }
    with pytest.raises(ValueError, match="invalid quantile mapping"):
        parse_quantile_columns(["p20=1.2"])
    with pytest.raises(ValueError, match="duplicate quantile column"):
        parse_quantile_columns(["p20=0.2", "p20=0.3"])


def test_non_finite_values_fail_closed() -> None:
    frame = pd.DataFrame(
        {
            "actual": [1.0, 2.0],
            "q10": [0.5, 1.5],
            "q25": [0.8, 1.8],
            "q50": [1.0, np.inf],
            "q75": [1.2, 2.2],
            "q90": [1.5, 2.5],
        }
    )

    with pytest.raises(ValueError, match="non-finite"):
        evaluate_quantiles(frame, target_column="actual")
