#!/usr/bin/env python3
"""Adjust p-values when evaluating many research hypotheses."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _validate(pvalues: np.ndarray) -> np.ndarray:
    values = np.asarray(pvalues, dtype=float)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("p-values must be a non-empty one-dimensional array")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("p-values must be finite and between 0 and 1")
    return values


def benjamini_hochberg(pvalues: np.ndarray) -> np.ndarray:
    """Return Benjamini-Hochberg false-discovery-rate adjusted p-values."""
    values = _validate(pvalues)
    count = len(values)
    order = np.argsort(values, kind="stable")
    ranked = values[order]
    adjusted_ranked = ranked * count / np.arange(1, count + 1)
    adjusted_ranked = np.minimum.accumulate(adjusted_ranked[::-1])[::-1]
    adjusted_ranked = np.clip(adjusted_ranked, 0.0, 1.0)
    adjusted = np.empty_like(adjusted_ranked)
    adjusted[order] = adjusted_ranked
    return adjusted


def holm(pvalues: np.ndarray) -> np.ndarray:
    """Return Holm family-wise-error adjusted p-values."""
    values = _validate(pvalues)
    count = len(values)
    order = np.argsort(values, kind="stable")
    ranked = values[order]
    adjusted_ranked = ranked * (count - np.arange(count))
    adjusted_ranked = np.maximum.accumulate(adjusted_ranked)
    adjusted_ranked = np.clip(adjusted_ranked, 0.0, 1.0)
    adjusted = np.empty_like(adjusted_ranked)
    adjusted[order] = adjusted_ranked
    return adjusted


def adjust_frame(
    frame: pd.DataFrame,
    *,
    pvalue_column: str,
    method: str,
    alpha: float = 0.05,
) -> pd.DataFrame:
    if pvalue_column not in frame.columns:
        raise ValueError(f"missing p-value column: {pvalue_column}")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")

    values = pd.to_numeric(frame[pvalue_column], errors="coerce").to_numpy(dtype=float)
    if method == "benjamini-hochberg":
        adjusted = benjamini_hochberg(values)
    elif method == "holm":
        adjusted = holm(values)
    else:
        raise ValueError("method must be 'benjamini-hochberg' or 'holm'")

    out = frame.copy()
    out["adjusted_p_value"] = adjusted
    out["reject_null"] = adjusted <= alpha
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--p-value-column", default="p_value")
    parser.add_argument(
        "--method",
        choices=("benjamini-hochberg", "holm"),
        default="benjamini-hochberg",
    )
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    result = adjust_frame(
        pd.read_csv(args.csv),
        pvalue_column=args.p_value_column,
        method=args.method,
        alpha=args.alpha,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out, index=False)


if __name__ == "__main__":
    main()
