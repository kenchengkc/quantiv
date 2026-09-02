#!/usr/bin/env python3
"""Statistical inference helpers for earnings event studies.

The default report treats realized-minus-priced move as the event-level effect,
uses a moving-block bootstrap for uncertainty, and a random sign-flip test for
a two-sided null of zero mean effect.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _clean(values: np.ndarray | pd.Series | list[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def moving_block_bootstrap_mean(
    values: np.ndarray | pd.Series | list[float],
    *,
    block_size: int = 4,
    draws: int = 10_000,
    seed: int = 7,
) -> np.ndarray:
    """Bootstrap means while preserving short-range event ordering dependence."""
    sample = _clean(values)
    if sample.size == 0:
        raise ValueError("at least one finite observation is required")
    if block_size < 1:
        raise ValueError("block_size must be >= 1")
    if draws < 1:
        raise ValueError("draws must be >= 1")

    block_size = min(block_size, sample.size)
    starts = np.arange(sample.size)
    rng = np.random.default_rng(seed)
    blocks_needed = int(np.ceil(sample.size / block_size))
    means = np.empty(draws, dtype=float)
    offsets = np.arange(block_size)
    for draw in range(draws):
        chosen_starts = rng.choice(starts, size=blocks_needed, replace=True)
        indices = (chosen_starts[:, None] + offsets[None, :]) % sample.size
        resampled = sample[indices.ravel()[: sample.size]]
        means[draw] = float(resampled.mean())
    return means


def sign_flip_pvalue(
    values: np.ndarray | pd.Series | list[float],
    *,
    draws: int = 20_000,
    seed: int = 11,
) -> float:
    """Two-sided randomization p-value for a zero-mean symmetric null."""
    sample = _clean(values)
    if sample.size == 0:
        raise ValueError("at least one finite observation is required")
    if draws < 1:
        raise ValueError("draws must be >= 1")

    observed = abs(float(sample.mean()))
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(draws):
        signs = rng.choice(np.array([-1.0, 1.0]), size=sample.size)
        if abs(float(np.mean(sample * signs))) >= observed:
            extreme += 1
    return (extreme + 1.0) / (draws + 1.0)


def summarize_event_study(
    realized_move: np.ndarray | pd.Series | list[float],
    priced_move: np.ndarray | pd.Series | list[float],
    *,
    block_size: int = 4,
    bootstrap_draws: int = 10_000,
    permutation_draws: int = 20_000,
    seed: int = 7,
) -> dict[str, Any]:
    realized = np.asarray(realized_move, dtype=float)
    priced = np.asarray(priced_move, dtype=float)
    if realized.shape != priced.shape:
        raise ValueError("realized_move and priced_move must have the same shape")
    valid = np.isfinite(realized) & np.isfinite(priced)
    realized = realized[valid]
    priced = priced[valid]
    if realized.size == 0:
        raise ValueError("at least one paired finite observation is required")

    excess = realized - priced
    bootstrap = moving_block_bootstrap_mean(
        excess, block_size=block_size, draws=bootstrap_draws, seed=seed
    )
    ci_low, ci_high = np.quantile(bootstrap, [0.025, 0.975])
    ratio = np.divide(realized, priced, out=np.full_like(realized, np.nan), where=priced != 0)
    return {
        "n": int(excess.size),
        "mean_realized_move": float(realized.mean()),
        "mean_priced_move": float(priced.mean()),
        "mean_excess_move": float(excess.mean()),
        "median_excess_move": float(np.median(excess)),
        "mean_realized_to_priced_ratio": float(np.nanmean(ratio)),
        "realized_exceeded_priced_rate": float(np.mean(realized > priced)),
        "bootstrap_95_ci_mean_excess": [float(ci_low), float(ci_high)],
        "sign_flip_pvalue": float(
            sign_flip_pvalue(excess, draws=permutation_draws, seed=seed + 1)
        ),
        "bootstrap": {
            "method": "circular_moving_block",
            "block_size": min(block_size, int(excess.size)),
            "draws": bootstrap_draws,
        },
        "null_test": {
            "method": "two_sided_random_sign_flip",
            "draws": permutation_draws,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--realized-column", default="realized_abs_move")
    parser.add_argument("--priced-column", default="straddle_pct")
    parser.add_argument("--block-size", type=int, default=4)
    parser.add_argument("--bootstrap-draws", type=int, default=10_000)
    parser.add_argument("--permutation-draws", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    frame = pd.read_csv(args.csv)
    report = summarize_event_study(
        frame[args.realized_column],
        frame[args.priced_column],
        block_size=args.block_size,
        bootstrap_draws=args.bootstrap_draws,
        permutation_draws=args.permutation_draws,
        seed=args.seed,
    )
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
