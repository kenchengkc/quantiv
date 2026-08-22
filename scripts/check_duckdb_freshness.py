#!/usr/bin/env python3
"""
Report the freshness of DuckDB views after a sync.

Shows the date range + row count for each core view and a per-day
breakdown of the last 10 trading days for options + OHLCV so you can
spot gaps.

Usage:
  .venv/bin/python3 scripts/check_duckdb_freshness.py
  .venv/bin/python3 scripts/check_duckdb_freshness.py --strict

With --strict, the script exits non-zero if any of the following:
  - Any required view is empty or missing
  - Options / OHLCV max date is more than --max-lag-days behind today
    (default 5 — covers a long weekend + DoltHub lag)
  - Any duplicates detected in the last 30 days
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import duckdb

DB_PATH = os.getenv(
    "DUCKDB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "quantiv.duckdb"),
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on any freshness/integrity problem")
    ap.add_argument("--max-lag-days", type=int, default=5,
                    help="In --strict mode, fail if options/ohlcv max date is older than this many days")
    args = ap.parse_args()

    conn = duckdb.connect(DB_PATH, read_only=True)
    problems: list[str] = []

    print(f"DuckDB: {DB_PATH}\n")

    today = date.today()
    lag_views = {"v_options", "v_ohlcv"}  # must be recent
    required_views = {"v_options", "v_ohlcv", "v_earnings"}  # must exist + have rows

    print("=== view range + row count ===")
    for v in ("v_options", "v_ohlcv", "v_earnings"):
        try:
            r = conn.execute(f"SELECT MIN(date), MAX(date), COUNT(*) FROM {v}").fetchone()
            print(f"  {v:12s} {r[0]} → {r[1]}   {r[2]:>12,} rows")
            if v in required_views and (r[2] or 0) == 0:
                problems.append(f"{v} is empty")
            if v in lag_views and r[1] is not None:
                lag = (today - r[1]).days
                if lag > args.max_lag_days:
                    problems.append(
                        f"{v} max date {r[1]} is {lag} days behind today "
                        f"(limit {args.max_lag_days})"
                    )
        except Exception as e:
            print(f"  {v:12s} ERROR: {e}")
            problems.append(f"{v} query failed: {e}")

    for label, sql in (
        (
            "options last 10 trading days",
            """
            SELECT date, COUNT(*) n, COUNT(DISTINCT act_symbol) tickers
            FROM v_options
            WHERE date >= (SELECT MAX(date) FROM v_options) - INTERVAL 14 DAY
            GROUP BY date ORDER BY date
            """,
        ),
        (
            "ohlcv last 10 trading days",
            """
            SELECT date, COUNT(*) n
            FROM v_ohlcv
            WHERE date >= (SELECT MAX(date) FROM v_ohlcv) - INTERVAL 14 DAY
            GROUP BY date ORDER BY date
            """,
        ),
    ):
        print(f"\n=== {label} ===")
        rows = conn.execute(sql).fetchall()
        for r in rows[-10:]:
            if len(r) == 3:
                print(f"  {r[0]}  {r[1]:>10,} rows  {r[2]:>5} tickers")
            else:
                print(f"  {r[0]}  {r[1]:>10,}")

    # Forward-looking earnings coverage — how far into the future do we see events?
    print("\n=== upcoming earnings ===")
    r = conn.execute(
        """
        SELECT MIN(date), MAX(date), COUNT(*)
        FROM v_earnings
        WHERE date >= CURRENT_DATE
        """
    ).fetchone()
    print(f"  CURRENT_DATE → {r[1]}   {r[2]:,} events in the future")

    # Duplicate detection. Most common cause: overlapping parquet layouts
    # (e.g. data_0.parquet from csv_to_parquet.py alongside per-date files
    # from sync_dolthub.py). Scans only the last 30 days for speed.
    print("\n=== duplicate check (last 30 days) ===")
    options_dups = conn.execute(
        """
        SELECT date,
               COUNT(*) AS total,
               COUNT(DISTINCT (act_symbol, expiration, strike, call_put)) AS uniq
        FROM v_options
        WHERE date >= (SELECT MAX(date) FROM v_options) - INTERVAL 30 DAY
        GROUP BY date
        HAVING total > uniq
        ORDER BY date
        """
    ).fetchall()
    if not options_dups:
        print("  v_options:    clean ✓")
    else:
        print("  v_options:    DUPLICATES FOUND")
        for d, total, uniq in options_dups:
            print(f"    {d}  {total:,} rows vs {uniq:,} unique keys ({total/uniq:.2f}x)")
        problems.append(f"v_options has duplicates on {len(options_dups)} day(s)")

    ohlcv_dups = conn.execute(
        """
        SELECT date, COUNT(*) AS total, COUNT(DISTINCT act_symbol) AS uniq
        FROM v_ohlcv
        WHERE date >= (SELECT MAX(date) FROM v_ohlcv) - INTERVAL 30 DAY
        GROUP BY date
        HAVING total > uniq
        ORDER BY date
        """
    ).fetchall()
    if not ohlcv_dups:
        print("  v_ohlcv:      clean ✓")
    else:
        print("  v_ohlcv:      DUPLICATES FOUND")
        for d, total, uniq in ohlcv_dups:
            print(f"    {d}  {total:,} rows vs {uniq:,} unique symbols ({total/uniq:.2f}x)")
        problems.append(f"v_ohlcv has duplicates on {len(ohlcv_dups)} day(s)")

    if args.strict and problems:
        print("\n❌ strict mode: freshness check failed")
        for p in problems:
            print(f"   - {p}")
        sys.exit(1)
    if args.strict:
        print("\n✅ strict mode: all checks passed")


if __name__ == "__main__":
    main()
