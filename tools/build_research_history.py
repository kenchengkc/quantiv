#!/usr/bin/env python3
"""Publish the source-level historical research universe."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
for candidate in (REPO_ROOT / "tools", ML_PACKAGE_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from build_earnings_events import build_earnings_events_table, create_duckdb_views  # noqa: E402
from frontend_data.research_history import (  # noqa: E402
    build_historical_event_universe,
    write_historical_event_universe,
)
from frontend_data.shared import DATA_DIR, EARNINGS_CSV, PUBLIC_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--earnings-csv", type=Path, default=EARNINGS_CSV)
    parser.add_argument(
        "--output",
        type=Path,
        default=PUBLIC_DIR / "research-history.json",
    )
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=None)
    parser.add_argument("--source-revision", default=os.getenv("GITHUB_SHA"))
    args = parser.parse_args()

    conn = duckdb.connect()
    conn.execute("PRAGMA memory_limit='4GB'")
    conn.execute("PRAGMA threads=4")
    try:
        build_earnings_events_table(conn, args.earnings_csv)
        create_duckdb_views(conn, args.data_dir)
        as_of_date = args.as_of_date
        if as_of_date is None:
            row = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()
            as_of_date = row[0]
        if as_of_date is None:
            raise RuntimeError("cannot build research history without an options as-of date")
        payload = build_historical_event_universe(
            conn,
            as_of_date,
            source_revision=args.source_revision,
        )
        write_historical_event_universe(args.output, payload)
    finally:
        conn.close()

    print(
        "Research history: "
        f"{payload['event_count']} eligible / {payload['audit']['candidate_event_count']} canonical events "
        f"({payload['audit']['excluded_event_count']} excluded) -> {args.output}"
    )
    print(f"Universe id: {payload['universe_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
