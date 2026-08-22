"""Leakage-resistant chronological splits for event-model training.

The prediction target and every sidecar column stay in the same DataFrame
until after the split. This prevents feature/target offsets when rows are
purged around the validation boundary.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def chronological_train_val_split(
    frame: pd.DataFrame,
    *,
    train_frac: float = 0.75,
    purge_days: int = 5,
    date_col: str = "__earnings_date",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Split complete event rows by date with a calendar-day embargo.

    All rows sharing the validation boundary date go to validation. Training
    ends strictly before ``validation_start - purge_days``, so events near the
    boundary cannot contribute features or targets to both sides.
    """
    if not 0 < train_frac < 1:
        raise ValueError("train_frac must be between 0 and 1")
    if purge_days < 0:
        raise ValueError("purge_days must be non-negative")
    if date_col not in frame.columns:
        raise ValueError(f"training data must include {date_col}")
    if len(frame) < 2:
        raise ValueError("training data must contain at least two rows")

    work = frame.copy()
    parsed_dates = pd.to_datetime(work[date_col], errors="coerce")
    if parsed_dates.isna().any():
        invalid_count = int(parsed_dates.isna().sum())
        raise ValueError(f"{date_col} contains {invalid_count} invalid date value(s)")
    work[date_col] = parsed_dates

    tie_breakers = [
        column
        for column in ("__symbol", "act_symbol", "symbol", "lead_days")
        if column in work.columns
    ]
    work = work.sort_values([date_col, *tie_breakers], kind="mergesort")

    split_idx = max(1, min(len(work) - 1, int(len(work) * train_frac)))
    validation_start = work.iloc[split_idx][date_col]
    train_cutoff = validation_start - pd.Timedelta(days=purge_days)

    validation = work.loc[work[date_col] >= validation_start].copy()
    training = work.loc[work[date_col] < train_cutoff].copy()

    if training.empty:
        raise ValueError(
            "purge window leaves no training rows; reduce purge_days or add more history"
        )
    if validation.empty:
        raise ValueError("chronological split leaves no validation rows")

    metadata: dict[str, Any] = {
        "method": "chronological_event_date_with_calendar_purge",
        "date_column": date_col,
        "requested_train_fraction": train_frac,
        "purge_days": purge_days,
        "rows_total": len(work),
        "rows_train": len(training),
        "rows_purged": len(work) - len(training) - len(validation),
        "rows_validation": len(validation),
        "train_start": training[date_col].min().date().isoformat(),
        "train_end": training[date_col].max().date().isoformat(),
        "validation_start": validation[date_col].min().date().isoformat(),
        "validation_end": validation[date_col].max().date().isoformat(),
    }
    return training, validation, metadata
