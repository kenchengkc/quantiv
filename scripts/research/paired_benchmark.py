#!/usr/bin/env python3
"""Compare model forecasts with a baseline on the same observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _bootstrap_mean_difference(
    difference: np.ndarray,
    *,
    draws: int,
    seed: int,
    clusters: np.ndarray | None = None,
) -> tuple[float, float, int | None]:
    """Return a reproducible percentile interval for paired error differences.

    With ``clusters`` set, complete clusters are resampled instead of individual
    rows. That keeps within-cluster dependence intact (for example repeated
    observations from one issuer or observations in one earnings week).
    """
    rng = np.random.default_rng(seed)
    boot = np.empty(draws, dtype=float)
    cluster_count: int | None = None
    if clusters is None:
        for draw in range(draws):
            indices = rng.integers(0, len(difference), size=len(difference))
            boot[draw] = float(np.mean(difference[indices]))
    else:
        cluster_values = np.asarray(clusters, dtype=object)
        unique_clusters = pd.unique(cluster_values)
        if len(unique_clusters) < 2:
            raise ValueError("cluster bootstrap requires at least two clusters")
        cluster_count = int(len(unique_clusters))
        indices_by_cluster = {
            cluster: np.flatnonzero(cluster_values == cluster)
            for cluster in unique_clusters
        }
        for draw in range(draws):
            sampled = rng.choice(unique_clusters, size=len(unique_clusters), replace=True)
            sampled_indices = np.concatenate(
                [indices_by_cluster[cluster] for cluster in sampled]
            )
            boot[draw] = float(np.mean(difference[sampled_indices]))
    low, high = np.quantile(boot, [0.025, 0.975])
    return float(low), float(high), cluster_count


def _paired_metrics(
    actual: np.ndarray,
    model: np.ndarray,
    baseline: np.ndarray,
    *,
    draws: int,
    seed: int,
    clusters: np.ndarray | None = None,
    cluster_label: str | None = None,
) -> dict[str, Any]:
    model_error = np.abs(actual - model)
    baseline_error = np.abs(actual - baseline)
    difference = model_error - baseline_error

    ci_low, ci_high, cluster_count = _bootstrap_mean_difference(
        difference,
        draws=draws,
        seed=seed,
        clusters=clusters,
    )

    model_sq = np.square(actual - model)
    baseline_sq = np.square(actual - baseline)
    result: dict[str, Any] = {
        "n": int(len(actual)),
        "model_mae": float(np.mean(model_error)),
        "baseline_mae": float(np.mean(baseline_error)),
        "model_rmse": float(np.sqrt(np.mean(model_sq))),
        "baseline_rmse": float(np.sqrt(np.mean(baseline_sq))),
        "mean_absolute_error_difference": float(np.mean(difference)),
        "mean_absolute_error_difference_95_ci": [ci_low, ci_high],
        "model_win_rate": float(np.mean(model_error < baseline_error)),
        "tie_rate": float(np.mean(model_error == baseline_error)),
        "bootstrap_unit": "rows" if clusters is None else f"cluster:{cluster_label}",
    }
    if cluster_count is not None:
        result["clusters"] = cluster_count
    return result


def compare_forecasts(
    frame: pd.DataFrame,
    *,
    actual_column: str,
    model_column: str,
    baseline_column: str,
    group_column: str | None = None,
    cluster_column: str | None = None,
    min_group_size: int = 20,
    draws: int = 5_000,
    seed: int = 17,
) -> dict[str, Any]:
    required = [actual_column, model_column, baseline_column]
    if group_column:
        required.append(group_column)
    if cluster_column and cluster_column not in required:
        required.append(cluster_column)
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
    if cluster_column:
        work = work.dropna(subset=[cluster_column])
    if work.empty:
        raise ValueError("no complete finite rows available")
    if not np.isfinite(work[numeric_columns].to_numpy(dtype=float)).all():
        raise ValueError("forecast comparison contains non-finite values")

    actual = work[actual_column].to_numpy(dtype=float)
    model = work[model_column].to_numpy(dtype=float)
    baseline = work[baseline_column].to_numpy(dtype=float)
    clusters = (
        work[cluster_column].astype(str).to_numpy(dtype=object)
        if cluster_column
        else None
    )
    report: dict[str, Any] = {
        "overall": _paired_metrics(
            actual,
            model,
            baseline,
            draws=draws,
            seed=seed,
            clusters=clusters,
            cluster_label=cluster_column,
        ),
        "groups": {},
        "bootstrap_draws": draws,
    }
    if cluster_column:
        report["cluster_column"] = cluster_column

    if group_column:
        groups: dict[str, Any] = {}
        for offset, (name, group) in enumerate(
            work.groupby(group_column, dropna=False, sort=True)
        ):
            if len(group) < min_group_size:
                continue
            group_clusters = (
                group[cluster_column].astype(str).to_numpy(dtype=object)
                if cluster_column
                else None
            )
            if group_clusters is not None and len(pd.unique(group_clusters)) < 2:
                continue
            groups[str(name)] = _paired_metrics(
                group[actual_column].to_numpy(dtype=float),
                group[model_column].to_numpy(dtype=float),
                group[baseline_column].to_numpy(dtype=float),
                draws=draws,
                seed=seed + offset + 1,
                clusters=group_clusters,
                cluster_label=cluster_column,
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
    parser.add_argument("--cluster-column")
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
        cluster_column=args.cluster_column,
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
