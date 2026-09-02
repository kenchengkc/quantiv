import numpy as np
import pandas as pd
import pytest

from research.multiple_testing import adjust_frame, benjamini_hochberg, holm


def test_benjamini_hochberg_matches_known_example() -> None:
    pvalues = np.array([0.01, 0.04, 0.03, 0.002])
    adjusted = benjamini_hochberg(pvalues)
    np.testing.assert_allclose(adjusted, [0.02, 0.04, 0.04, 0.008])


def test_holm_matches_known_example() -> None:
    pvalues = np.array([0.01, 0.04, 0.03, 0.002])
    adjusted = holm(pvalues)
    np.testing.assert_allclose(adjusted, [0.03, 0.06, 0.06, 0.008])


def test_adjust_frame_preserves_rows_and_marks_rejections() -> None:
    frame = pd.DataFrame(
        {
            "signal": ["a", "b", "c", "d"],
            "p_value": [0.001, 0.02, 0.2, 0.8],
        }
    )
    result = adjust_frame(
        frame,
        pvalue_column="p_value",
        method="benjamini-hochberg",
        alpha=0.05,
    )

    assert result["signal"].tolist() == frame["signal"].tolist()
    assert result["reject_null"].tolist() == [True, True, False, False]


def test_invalid_p_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        benjamini_hochberg(np.array([0.1, 1.2]))
