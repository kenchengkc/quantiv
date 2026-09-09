#!/usr/bin/env python3
"""Record completion evidence for a successful earnings-calendar source fetch.

This script is intentionally run immediately after ``sync_dolthub.py --earnings``
in the same fail-fast workflow step. It does not fetch or mutate calendar rows;
it records which local source snapshot the successful fetch produced so an
independent calendar publication cannot treat restored metadata as fresh.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CALENDAR = REPO_ROOT / "data" / "earnings_calendar.csv"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "dolthub_earnings_metadata.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_evidence(calendar: Path, observed_at: datetime | None = None) -> dict:
    if not calendar.is_file() or calendar.stat().st_size == 0:
        raise RuntimeError(f"earnings calendar is missing or empty: {calendar}")
    rows = 0
    symbols: set[str] = set()
    dates: list[str] = []
    with calendar.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"act_symbol", "date"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"earnings calendar missing columns: {sorted(missing)}")
        for row in reader:
            symbol = str(row.get("act_symbol") or "").strip().upper()
            day = str(row.get("date") or "")[:10]
            if not symbol or not day:
                continue
            rows += 1
            symbols.add(symbol)
            dates.append(day)
    if rows == 0:
        raise RuntimeError("earnings calendar contains no usable rows")
    stamp = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema": "quantiv.calendar-source-evidence.v1",
        "provider": "dolthub",
        "synced_at": stamp.isoformat().replace("+00:00", "Z"),
        "rows": rows,
        "symbols": len(symbols),
        "date_min": min(dates),
        "date_max": max(dates),
        "source_file_sha256": sha256_file(calendar),
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_evidence(args.calendar)
    atomic_json(args.output, payload)
    print(
        f"recorded DoltHub calendar fetch evidence: {payload['rows']} rows, "
        f"{payload['symbols']} symbols at {payload['synced_at']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
