import numpy as np
import pytest

from ml.quantiles import rearrange_quantile_array, rearrange_quantile_mapping


def test_rearrange_quantile_mapping_orders_and_clips_absolute_moves():
    assert rearrange_quantile_mapping(
        {10: 0.04, 25: -0.01, 50: 0.03, 75: 0.09, 90: 0.07}
    ) == {10: 0.0, 25: 0.03, 50: 0.04, 75: 0.07, 90: 0.09}


def test_rearrange_quantile_mapping_drops_nonfinite_values():
    assert rearrange_quantile_mapping({10: 0.02, 50: float("nan"), 90: 0.08}) == {
        10: 0.02,
        90: 0.08,
    }


def test_rearrange_quantile_array_operates_per_row():
    raw = np.array([[0.05, 0.03, 0.08], [-0.02, 0.02, 0.01]])
    expected = np.array([[0.03, 0.05, 0.08], [0.0, 0.01, 0.02]])
    np.testing.assert_allclose(rearrange_quantile_array(raw), expected)


def test_rearrange_quantile_array_rejects_nonfinite_values():
    with pytest.raises(ValueError, match="finite"):
        rearrange_quantile_array(np.array([[0.02, np.nan, 0.08]]))
