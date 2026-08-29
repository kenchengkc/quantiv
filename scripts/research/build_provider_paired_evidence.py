#!/usr/bin/env python3
"""Build policy-compatible evidence from row-level paired OOS predictions.

The input must contain the control and signal-enabled candidate on identical
event/horizon/fold rows. This command does not collect data or enable a signal;
it produces a content-addressed decision artifact that can be reviewed and
pinned in ``config/provider_signal_policy.json`` only when every gate passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

from ml.provider_signal_policy import (  # noqa: E402
    load_provider_signal_policy,
    validate_paired_report,
)

SCHEMA = "quantiv.provider-paired-test.v1"
KEY_COLUMNS = ["act_symbol", "earnings_date", "model_horizon", "fold"]
NUMERIC_COLUMNS = ["actual", "straddle", "control_prediction", "candidate_prediction"]
SLICE_COLUMNS = ["sector", "volatility_regime", "liquidity", "dte_bucket"]
DATE_COLUMNS = ["earnings_date", "train_end", "test_start", "test_end"]


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _paired_t_stat(values: pd.Series) -> float | None:
    data = values.to_numpy(dtype=float)
    if len(data) < 2:
        return None
    standard_deviation = float(np.std(data, ddof=1))
    if not math.isfinite(standard_deviation) or standard_deviation <= 0:
        return None
    return float(np.mean(data) / (standard_deviation / math.sqrt(len(data))))


def _prepare(frame: pd.DataFrame, *, purge_days: int) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    required = set(KEY_COLUMNS + NUMERIC_COLUMNS + DATE_COLUMNS + SLICE_COLUMNS)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"paired prediction input is missing columns: {missing}")
    if purge_days < 1:
        raise ValueError("purge_days must be at least one")

    out = frame.copy()
    out["act_symbol"] = out["act_symbol"].fillna("").astype(str).str.strip().str.upper()
    if out["act_symbol"].eq("").any():
        raise ValueError("paired prediction input contains blank symbols")
    for column in DATE_COLUMNS:
        out[column] = pd.to_datetime(out[column], errors="raise").dt.normalize()
    for column in NUMERIC_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if not np.isfinite(out[NUMERIC_COLUMNS].to_numpy(dtype=float)).all():
        raise ValueError("paired prediction input contains non-finite model evidence")
    if (out[["actual", "control_prediction", "candidate_prediction"]] < 0).any().any():
        raise ValueError("actual and model predictions must be nonnegative")
    if (out["straddle"] <= 0).any():
        raise ValueError("straddle baseline must be positive")
    if out.duplicated(KEY_COLUMNS).any():
        raise ValueError("paired prediction input contains duplicate event/horizon/fold keys")

    split_rows: list[dict[str, Any]] = []
    for fold, group in out.groupby("fold", sort=True):
        boundaries = group[["train_end", "test_start", "test_end"]].drop_duplicates()
        if len(boundaries) != 1:
            raise ValueError(f"fold {fold} contains inconsistent split boundaries")
        train_end, test_start, test_end = boundaries.iloc[0]
        if train_end > test_start - pd.Timedelta(days=purge_days):
            raise ValueError(f"fold {fold} violates the {purge_days}-day purge")
        if test_start > test_end:
            raise ValueError(f"fold {fold} has an inverted test window")
        if ((group["earnings_date"] < test_start) | (group["earnings_date"] > test_end)).any():
            raise ValueError(f"fold {fold} contains events outside its test window")
        split_rows.append(
            {
                "fold": str(fold),
                "train_end": train_end.date().isoformat(),
                "test_start": test_start.date().isoformat(),
                "test_end": test_end.date().isoformat(),
                "rows": len(group),
                "events": int(group[["act_symbol", "earnings_date"]].drop_duplicates().shape[0]),
                "purge_days": purge_days,
            }
        )
    return out, split_rows


def build_paired_report(
    frame: pd.DataFrame,
    *,
    signal: str,
    incremental_monthly_cost_usd: float,
    purge_days: int = 5,
    generated_at: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    policy = load_provider_signal_policy()
    if signal not in policy["signals"]:
        raise ValueError(f"unknown provider signal: {signal}")
    prepared, split_rows = _prepare(frame, purge_days=purge_days)
    requirements = policy["requirements"]

    prepared["control_error"] = (
        prepared["actual"] - prepared["control_prediction"]
    ).abs()
    prepared["candidate_error"] = (
        prepared["actual"] - prepared["candidate_prediction"]
    ).abs()
    prepared["straddle_error"] = (prepared["actual"] - prepared["straddle"]).abs()
    prepared["error_delta"] = prepared["candidate_error"] - prepared["control_error"]

    event_deltas = prepared.groupby(
        ["act_symbol", "earnings_date"], sort=True
    )["error_delta"].mean()
    straddle_mae = float(prepared["straddle_error"].mean())
    control_mae = float(prepared["control_error"].mean())
    candidate_mae = float(prepared["candidate_error"].mean())

    minimum_slice_events = int(requirements.get("minimum_slice_events", 30))
    slice_results: dict[str, list[dict[str, Any]]] = {}
    worst_slice_regression = -math.inf
    for column in SLICE_COLUMNS:
        rows: list[dict[str, Any]] = []
        labels = prepared[column].fillna("unknown").astype(str)
        for label, group in prepared.assign(_slice=labels).groupby("_slice", sort=True):
            event_count = int(group[["act_symbol", "earnings_date"]].drop_duplicates().shape[0])
            if event_count < minimum_slice_events:
                continue
            group_control = float(group["control_error"].mean())
            group_candidate = float(group["candidate_error"].mean())
            regression = (
                (group_candidate / group_control - 1.0) * 100.0
                if group_control > 0
                else math.inf
            )
            worst_slice_regression = max(worst_slice_regression, regression)
            rows.append(
                {
                    "slice": label,
                    "events": event_count,
                    "control_mae": group_control,
                    "candidate_mae": group_candidate,
                    "mae_regression_pct": regression,
                }
            )
        slice_results[column] = rows
    if worst_slice_regression == -math.inf:
        worst_slice_regression = math.inf

    keys = [
        {
            "act_symbol": row.act_symbol,
            "earnings_date": row.earnings_date.date().isoformat(),
            "model_horizon": int(row.model_horizon),
            "fold": str(row.fold),
        }
        for row in prepared.sort_values(KEY_COLUMNS)[KEY_COLUMNS].itertuples(index=False)
    ]
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "signal": signal,
        "status": "passed",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_sha256,
        "paired_keys_sha256": _canonical_hash(keys),
        "split_audit_sha256": _canonical_hash(split_rows),
        "split_audit": split_rows,
        "sample": {
            "rows": len(prepared),
            "events": int(prepared[["act_symbol", "earnings_date"]].drop_duplicates().shape[0]),
            "walk_forward_folds": int(prepared["fold"].nunique()),
            "horizons": sorted(int(value) for value in prepared["model_horizon"].unique()),
        },
        "straddle": {"mae": straddle_mae},
        "control": {
            "mae": control_mae,
            "straddle_relative_mae": control_mae / straddle_mae if straddle_mae else None,
        },
        "candidate": {
            "mae": candidate_mae,
            "straddle_relative_mae": candidate_mae / straddle_mae if straddle_mae else None,
        },
        "mae_improvement_pct": (control_mae - candidate_mae) / control_mae * 100.0,
        "paired_error_delta": {
            "mean": float(event_deltas.mean()),
            "standard_deviation": float(event_deltas.std(ddof=1)),
            "t_stat": _paired_t_stat(event_deltas),
            "unit": "event_mean_across_horizons",
        },
        "minimum_slice_events": minimum_slice_events,
        "slice_results": slice_results,
        "worst_slice_mae_regression_pct": worst_slice_regression,
        "incremental_monthly_cost_usd": float(incremental_monthly_cost_usd),
    }
    failures = validate_paired_report(signal, report, requirements)
    if failures:
        report["status"] = "failed"
        report["gate_failures"] = failures
    report["report_id"] = _canonical_hash(report)
    return report


def _read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError("paired predictions must be CSV or Parquet")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--purge-days", type=int, default=5)
    parser.add_argument("--incremental-monthly-cost-usd", type=float, default=0.0)
    args = parser.parse_args()

    try:
        report = build_paired_report(
            _read_frame(args.input),
            signal=args.signal,
            incremental_monthly_cost_usd=args.incremental_monthly_cost_usd,
            purge_days=args.purge_days,
            source_sha256=_file_hash(args.input),
        )
    except (OSError, ValueError) as exc:
        print(f"Provider paired evidence failed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
