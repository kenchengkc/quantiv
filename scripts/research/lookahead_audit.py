#!/usr/bin/env python3
"""Audit feature timestamps for look-ahead bias."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def audit_lookahead(
    frame: pd.DataFrame,
    *,
    decision_column: str,
    available_column: str,
    feature_column: str | None = None,
    id_column: str | None = None,
) -> dict[str, Any]:
    required = [decision_column, available_column]
    for optional in (feature_column, id_column):
        if optional:
            required.append(optional)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    work = frame[required].copy()
    work[decision_column] = pd.to_datetime(work[decision_column], utc=True, errors="coerce")
    work[available_column] = pd.to_datetime(work[available_column], utc=True, errors="coerce")

    missing_timestamp = work[decision_column].isna() | work[available_column].isna()
    valid = work.loc[~missing_timestamp].copy()
    violation = valid[available_column] > valid[decision_column]
    lead_seconds = (valid[available_column] - valid[decision_column]).dt.total_seconds()

    violations: list[dict[str, Any]] = []
    for index in valid.index[violation]:
        row = valid.loc[index]
        record: dict[str, Any] = {
            "row": int(index) if isinstance(index, int) else str(index),
            "decision_at": row[decision_column].isoformat(),
            "available_at": row[available_column].isoformat(),
            "lead_seconds": float(
                (row[available_column] - row[decision_column]).total_seconds()
            ),
        }
        if feature_column:
            record["feature"] = str(row[feature_column])
        if id_column:
            record["id"] = str(row[id_column])
        violations.append(record)

    by_feature: dict[str, dict[str, int]] = {}
    if feature_column:
        grouped = valid.assign(_violation=violation).groupby(feature_column, dropna=False)
        for feature, group in grouped:
            by_feature[str(feature)] = {
                "rows": int(len(group)),
                "violations": int(group["_violation"].sum()),
            }

    return {
        "rows": int(len(work)),
        "rows_with_valid_timestamps": int(len(valid)),
        "rows_with_missing_timestamps": int(missing_timestamp.sum()),
        "lookahead_violations": int(violation.sum()),
        "max_lead_seconds": float(lead_seconds.max()) if len(lead_seconds) else None,
        "passed": bool(not missing_timestamp.any() and not violation.any()),
        "violations": violations,
        "by_feature": by_feature,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--decision-column", default="decision_at")
    parser.add_argument("--available-column", default="available_at")
    parser.add_argument("--feature-column")
    parser.add_argument("--id-column")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = audit_lookahead(
        pd.read_csv(args.csv),
        decision_column=args.decision_column,
        available_column=args.available_column,
        feature_column=args.feature_column,
        id_column=args.id_column,
    )
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
    else:
        print(payload, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
