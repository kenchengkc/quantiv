#!/usr/bin/env python3
"""Gate the after-close event-freeze workflow against NYSE session state."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from market_sessions import EASTERN, is_us_market_session, us_market_close  # noqa: E402


def _emit(name: str, value: str) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=("schedule", "data"), required=True)
    ap.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "data" / "quantiv.duckdb",
    )
    ap.add_argument(
        "--now",
        help="test override as ISO timestamp; defaults to current time",
    )
    args = ap.parse_args()

    now = (
        datetime.fromisoformat(args.now)
        if args.now
        else datetime.now(EASTERN)
    )
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    now = now.astimezone(EASTERN)
    session_day = now.date()

    if not is_us_market_session(session_day):
        _emit("run", "false")
        _emit("reason", "not_market_session")
        return 0

    close = datetime.combine(
        session_day,
        us_market_close(session_day),
        tzinfo=EASTERN,
    )
    if args.phase == "schedule":
        # Two UTC cron entries cover EDT and EST. Exactly one should land in
        # this 15-75 minute post-close settlement window.
        should_run = close + timedelta(minutes=15) <= now <= close + timedelta(minutes=75)
        _emit("run", "true" if should_run else "false")
        _emit("reason", "post_close_window" if should_run else "outside_post_close_window")
        _emit("session_date", session_day.isoformat())
        return 0

    if not args.db.exists():
        _emit("ready", "false")
        _emit("reason", "missing_duckdb")
        return 0

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        ohlcv = conn.execute("SELECT MAX(date) FROM v_ohlcv").fetchone()[0]
        options = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()[0]
    finally:
        conn.close()

    ready = ohlcv == session_day and options == session_day
    _emit("ready", "true" if ready else "false")
    _emit(
        "reason",
        "current_session_inputs"
        if ready
        else f"source_lag_ohlcv_{ohlcv}_options_{options}",
    )
    _emit("session_date", session_day.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
