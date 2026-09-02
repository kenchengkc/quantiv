#!/usr/bin/env python3
"""Evaluate quantile forecast calibration from a flat research export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_QUANTILES = {
    "q10": 0.10,
    "q25": 0.25,
    "q50": 0.50,
    "q75": 0.75,
    "q90": 0.90,
}


def pinball_loss(actual: np.ndarray, forecast: np.ndarray, quantile: float) -> float:
    error = actual - forecast
    loss = np.maximum(quantile * error, (quantile - 1.0) * error)
    return float(np.mean(loss))


def evaluate_quantiles(
    frame: pd.DataFrame,
    *,
    target_column: str,
    quantile_columns: dict[str, float] | None = None,
) -> dict[str, Any]:
    quantiles = quantile_columns or DEFAULT_QUANTILES
    required = [target_column, *quantiles]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    work = frame[required].apply(pd.to_numeric, errors="coerce").dropna()
    if work.empty:
        raise ValueError("no complete finite rows available")
    if not np.isfinite(work.to_numpy(dtype=float)).all():
        raise ValueError("quantile evaluation contains non-finite values")

    actual = work[target_column].to_numpy(dtype=float)
    forecasts = {name: work[name].to_numpy(dtype=float) for name in quantiles}

    ordered_names = sorted(quantiles, key=lambda name: quantiles[name])
    rows: dict[str, dict[str, float]] = {}
    calibration_errors: list[float] = []
    for name in ordered_names:
        level = quantiles[name]
        forecast = forecasts[name]
        empirical = float(np.mean(actual <= forecast))
        calibration_error = empirical - level
        calibration_errors.append(abs(calibration_error))
        rows[name] = {
            "nominal": level,
            "empirical": empirical,
            "calibration_error": calibration_error,
            "pinball_loss": pinball_loss(actual, forecast, level),
        }

    stacked = np.column_stack([forecasts[name] for name in ordered_names])
    crossing = np.any(np.diff(stacked, axis=1) < 0, axis=1)

    intervals: dict[str, dict[str, float]] = {}
    for low_name, high_name in (("q10", "q90"), ("q25", "q75")):
        if low_name not in forecasts or high_name not in forecasts:
            continue
        low = forecasts[low_name]
        high = forecasts[high_name]
        nominal = quantiles[high_name] - quantiles[low_name]
        empirical = float(np.mean((actual >= low) & (actual <= high)))
        intervals[f"{low_name}_{high_name}"] = {
            "nominal_coverage": nominal,
            "empirical_coverage": empirical,
            "coverage_error": empirical - nominal,
            "mean_width": float(np.mean(high - low)),
        }

    return {
        "n": int(len(work)),
        "quantiles": rows,
        "mean_absolute_calibration_error": float(np.mean(calibration_errors)),
        "quantile_crossing_rate": float(np.mean(crossing)),
        "intervals": intervals,
    }


def parse_quantile_columns(values: list[str]) -> dict[str, float]:
    if not values:
        return DEFAULT_QUANTILES.copy()

    parsed: dict[str, float] = {}
    for value in values:
        parts = value.split("=", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid quantile mapping: {value}")
        name, raw_level = parts
        try:
            level = float(raw_level)
        except ValueError as exc:
            raise ValueError(f"invalid quantile mapping: {value}") from exc
        if not name or not 0.0 < level < 1.0:
            raise ValueError(f"invalid quantile mapping: {value}")
        if name in parsed:
            raise ValueError(f"duplicate quantile column: {name}")
        parsed[name] = level

    if len(set(parsed.values())) != len(parsed):
        raise ValueError("quantile levels must be unique")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--target", required=True, help="realized target column")
    parser.add_argument(
        "--quantile",
        action="append",
        default=[],
        metavar="COLUMN=LEVEL",
        help="forecast column and nominal quantile; repeat as needed",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = evaluate_quantiles(
        pd.read_csv(args.csv),
        target_column=args.target,
        quantile_columns=parse_quantile_columns(args.quantile),
    )
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
