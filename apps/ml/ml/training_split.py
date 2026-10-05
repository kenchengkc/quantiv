"""Availability-aware chronological splits for event-model training.

The prediction target and every sidecar column stay in the same DataFrame
until after the split. This prevents feature/target offsets when rows are
purged around the validation boundary.
"""

from __future__ import annotations

from typing import Any

import hashlib
import json
from datetime import date, datetime

import numpy as np
import pandas as pd


def _dates(values: pd.Series, name: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", utc=True).dt.tz_localize(None)
    if parsed.isna().any():
        raise ValueError(f"{name} contains {int(parsed.isna().sum())} invalid date value(s)")
    return parsed


def temporal_dates(
    frame: pd.DataFrame, *, horizon_days: int | None = None,
    label_lag_days: int = 5, purge_days: int = 5, date_col: str = "__earnings_date",
) -> tuple[pd.Series, pd.Series, str]:
    """Return prediction/label dates, failing closed on incomplete sidecars.

    Older callers without a declared horizon retain their original event-date
    embargo. Legacy model callers must pass the horizon so the inferred label
    lag and prediction lead time are both included in the cutoff.
    """
    if label_lag_days < 0 or purge_days < 0 or (horizon_days is not None and horizon_days < 0):
        raise ValueError("horizon and availability windows must be non-negative")
    events = _dates(frame[date_col], date_col)
    sidecars = {"__snapshot_date", "__label_available_at"}
    present = sidecars.intersection(frame.columns)
    if present and present != sidecars:
        raise ValueError("temporal evidence must include both snapshot and label availability")
    if present:
        snapshots = _dates(frame["__snapshot_date"], "__snapshot_date")
        labels = _dates(frame["__label_available_at"], "__label_available_at")
        if (snapshots >= events).any() or (labels < events).any():
            raise ValueError("snapshot/label availability is inconsistent with earnings date")
        return snapshots, labels, "observed_label_availability"
    snapshots = events - pd.Timedelta(days=horizon_days or 0)
    lag = max(purge_days, label_lag_days) if horizon_days is not None else purge_days
    labels = events + pd.Timedelta(days=lag)
    return snapshots, labels, "conservative_legacy_horizon_and_label_lag"


def half_life_weights(
    dates: pd.Series, cutoff: pd.Timestamp, half_life_years: float,
) -> np.ndarray | None:
    if half_life_years <= 0:
        return None
    age = (pd.Timestamp(cutoff) - pd.to_datetime(dates)).dt.total_seconds().clip(lower=0).to_numpy()
    return np.exp(-np.log(2) * age / (365.0 * 86400 * half_life_years))


def training_row_digest(frame: pd.DataFrame) -> str:
    """Bind exact row values independently of parquet encoding and row order."""
    def scalar(value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, (datetime, date, pd.Timestamp)):
            return value.isoformat()
        if isinstance(value, (float, np.floating)):
            return {"float_hex": float(value).hex()}
        if isinstance(value, np.generic):
            return value.item()
        return value

    records = [{column: scalar(value) for column, value in row.items()}
               for row in frame.reindex(sorted(frame.columns), axis=1).to_dict(orient="records")]
    rows = sorted(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in records)
    return "sha256:" + hashlib.sha256("\n".join(rows).encode()).hexdigest()


def eligible_mature_rows(
    frame: pd.DataFrame, *, as_of: str | pd.Timestamp, horizon_days: int,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    snapshots, labels, _ = temporal_dates(frame, horizon_days=horizon_days)
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    eligible = labels <= cutoff
    return frame.loc[eligible].copy(), snapshots.loc[eligible], labels.loc[eligible]


def chronological_train_val_split(
    frame: pd.DataFrame,
    *,
    train_frac: float = 0.75,
    purge_days: int = 5,
    date_col: str = "__earnings_date",
    horizon_days: int | None = None,
    label_lag_days: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Group validation by event date, then purge by prediction availability.

    All rows sharing the event boundary stay in validation. Observed training
    labels must exist strictly before its first prediction snapshot. Legacy
    rows use a conservative declared horizon plus label-availability lag.
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
    validation = work.loc[work[date_col] >= validation_start].copy()
    snapshots, labels, availability_basis = temporal_dates(
        work, horizon_days=horizon_days, label_lag_days=label_lag_days,
        purge_days=purge_days, date_col=date_col,
    )
    prediction_cutoff = snapshots.loc[validation.index].min()
    training = work.loc[(work[date_col] < validation_start) & (labels < prediction_cutoff)].copy()

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
        "validation_snapshot_start": prediction_cutoff.date().isoformat(),
        "train_label_available_end": labels.loc[training.index].max().date().isoformat(),
        "availability_basis": availability_basis,
        "horizon_days": horizon_days,
    }
    return training, validation, metadata
