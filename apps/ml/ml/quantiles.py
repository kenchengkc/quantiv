"""Post-processing helpers for absolute-move quantile forecasts."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np


def rearrange_quantile_mapping(predictions: Mapping[int, float]) -> dict[int, float]:
    """Return finite quantiles in nondecreasing order, clipped at zero.

    Independently trained quantile heads occasionally cross. Monotone
    rearrangement preserves the set of predictions while making the served
    distribution valid for a nonnegative absolute-move target.
    """
    keys = sorted(
        key
        for key, value in predictions.items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    values = sorted(max(0.0, float(predictions[key])) for key in keys)
    return dict(zip(keys, values))


def rearrange_quantile_array(predictions: np.ndarray) -> np.ndarray:
    """Apply nonnegative monotone rearrangement to each prediction row."""
    values = np.asarray(predictions, dtype=float)
    if values.ndim != 2:
        raise ValueError("quantile predictions must be a two-dimensional array")
    if not np.isfinite(values).all():
        raise ValueError("quantile predictions must all be finite")
    return np.sort(np.clip(values, 0.0, None), axis=1)
