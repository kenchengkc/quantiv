#!/usr/bin/env python3
"""Promote a validated after-close scoring candidate into the event ledger."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from daily_score import update_event_forecast_archive  # noqa: E402
from event_forecast_ledger import annotate_forecasts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidate", type=Path)
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data",
    )
    args = ap.parse_args()

    frame = pd.read_parquet(args.candidate)
    if frame.empty:
        print("No event-freeze candidate rows; nothing to promote.")
        return 0

    audited = annotate_forecasts(frame)
    eligible = audited[audited["freeze_eligible"].fillna(False).astype(bool)]
    rejected = audited[~audited["freeze_eligible"].fillna(False).astype(bool)]

    if rejected.empty:
        print(f"Event-freeze candidate: {len(eligible)} eligible row(s), 0 rejected")
    else:
        reasons = (
            rejected["freeze_ineligible_reason"]
            .fillna("unknown")
            .value_counts()
            .to_dict()
        )
        print(
            f"Event-freeze candidate: {len(eligible)} eligible, "
            f"{len(rejected)} rejected ({reasons})"
        )

    archive = update_event_forecast_archive(
        frame,
        args.data_dir / "forecasts",
    )
    if archive is None:
        raise SystemExit("No event forecast archive was produced")
    print(f"Promoted validated candidate into immutable ledger → {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
