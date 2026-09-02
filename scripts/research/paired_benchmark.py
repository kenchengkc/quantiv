#!/usr/bin/env python3
"""Compare model forecasts with a baseline on the same observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _paired_metrics(
    actual: np.ndarray,
    model: np.ndarray,
    baseline: np.ndarray,
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    model_error = np.abs(actual - model)
    baseline_error = np.abs(actual - baseline)
    difference = model_error - baseline_error

    rng = np.random.default_rng(seed)
    boot = np.empty(draws, dtype=float)
    for draw in range(draws):
        indices = rng.integers(0, len(difference), size=len(difference))
        boot[draw] = float(np.mean(difference[indices]))
    ci_low, ci_high = np.quantile(boot, [0.025, 0.975])

    model_sq = np.square(actual - model)
    baseline_sq = np.square(actual - baseline)
    return {
        "n": int(len(actual)),
        "model_mae": float(np.mean(model_error)),
        "baseline_mae": float(np.mean(baseline_error)),
        "model_rmse": float(np.sqrt(np.mean(model_sq))),
        "baseline_rmse": float(np.sqrt(np.mean(baseline_sq))),
        "mean_absolute_error_difference": float(np.mean(difference)),
        "mean_absolute_error_difference_95_ci": [float(ci_low), float(ci_high)],
        "model_win_rate": float(np.mean(model_error < baseline_error)),
        "tie_rate": float(np.mean(model_error == baseline_error)),
    }


def compare_forecasts(
    frame: pd.DataFrame,
    *,
    actual_column: str,
    model_column: str,
    baseline_column: str,
    group_column: str | None = None,
    min_group_size: int = 20,
    draws: int = 5_000,
    seed: int = 17,
) -> dict[str, Any]:
    required = [actual_column, model_column, baseline_column]
    if group_column:
        required.append(group_column)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    if draws < 1:
        raise ValueError("draws must be >= 1")
    if min_group_size < 1:
        raise ValueError("min_group_size must be >= 1")

    numeric_columns = [actual_column, model_column, baseline_column]
    work = frame[required].copy()
    for column in numeric_columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=numeric_columns)
    if work.empty:
        raise ValueError("no complete finite rows available")
    if not np.isfinite(work[numeric_columns].to_numpy(dtype=float)).all():
        raise ValueError("forecast comparison contains non-finite values")

    actual = work[actual_column].to_numpy(dtype=float)
    model = work[model_column].to_numpy(dtype=float)
    baseline = work[baseline_column].to_numpy(dtype=float)
    report: dict[str, Any] = {
        "overall": _paired_metrics(
            actual,
            model,
            baseline,
            draws=draws,
            seed=seed,
        ),
        "groups": {},
        "bootstrap_draws": draws,
    }

    if group_column:
        groups: dict[str, Any] = {}
        for offset, (name, group) in enumerate(
            work.groupby(group_column, dropna=False, sort=True)
        ):
            if len(group) < min_group_size:
                continue
            groups[str(name)] = _paired_metrics(
                group[actual_column].to_numpy(dtype=float),
                group[model_column].to_numpy(dtype=float),
                group[baseline_column].to_numpy(dtype=float),
                draws=draws,
                seed=seed + offset + 1,
            )
        report["groups"] = groups
        report["group_column"] = group_column
        report["min_group_size"] = min_group_size

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--actual", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--group-column")
    parser.add_argument("--min-group-size", type=int, default=20)
    parser.add_argument("--draws", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = compare_forecasts(
        pd.read_csv(args.csv),
        actual_column=args.actual,
        model_column=args.model,
        baseline_column=args.baseline,
        group_column=args.group_column,
        min_group_size=args.min_group_size,
        draws=args.draws,
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
