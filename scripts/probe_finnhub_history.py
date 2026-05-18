#!/usr/bin/env python3
"""
Probe Finnhub's /calendar/earnings endpoint to see how deep historical
coverage actually goes on the free tier when querying with ?symbol=.

Why this exists:
  Finnhub's docs are ambiguous about the free-tier historical window.
  The docs page shows an example like
      /calendar/earnings?from=2024-03-01&to=2026-08-09&symbol=AAPL
  spanning >2 years, while public guidance says free is limited to ~1
  month. Either the docs are wrong, the per-symbol query is exempt, or
  the cap applies per-request only. A single probe call answers this
  without committing changes to the data pipeline.

Behavior:
  - One GET against a wide date range for AAPL by default.
  - Prints rows returned, oldest/newest date, and a 1-line verdict
    classifying the entitlement as ["deep" >4 events, "moderate" 2-4,
    "shallow" 0-1].
  - Costs exactly 1 Finnhub call. Run outside market hours to leave
    headroom for live-quote workers (60 calls/min ceiling).

Usage:
  FINNHUB_API_KEY=... python scripts/probe_finnhub_history.py
  python scripts/probe_finnhub_history.py --symbol NVDA --years 3
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://finnhub.io/api/v1/calendar/earnings"


def load_local_env() -> None:
    for path in [
        REPO_ROOT / "config" / ".env.local",
        REPO_ROOT / "config" / ".env.production",
    ]:
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch(token: str, symbol: str, start: date, end: date) -> list[dict]:
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "symbol": symbol.upper(),
        "token": token,
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("earningsCalendar") or []


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--years", type=int, default=3,
                        help="How many years back to ask for (default 3).")
    parser.add_argument(
        "--walk",
        action="store_true",
        help=(
            "Walk 90-day historical windows backward to test whether narrow "
            "windows unlock past quarters even when a wide window returns just "
            "one event. Costs N calls where N = years * 4. Use after a wide "
            "probe returns 'shallow' to decide if backfill-by-walking is viable."
        ),
    )
    args = parser.parse_args()

    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        print("✗ FINNHUB_API_KEY missing", file=sys.stderr)
        return 1

    today = date.today()
    symbol = args.symbol.upper()

    # Test 1: wide window (the original probe).
    wide_start = today - timedelta(days=args.years * 365)
    wide_end = today + timedelta(days=30)
    print(f"GET {BASE_URL}?from={wide_start}&to={wide_end}&symbol={symbol}")
    try:
        wide_rows = fetch(token, symbol, wide_start, wide_end)
    except requests.HTTPError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"\nWide window: returned {len(wide_rows)} events")
    if wide_rows:
        dates = sorted(r.get("date") for r in wide_rows if r.get("date"))
        oldest, newest = dates[0], dates[-1]
        span_days = (date.fromisoformat(newest[:10]) - date.fromisoformat(oldest[:10])).days
        print(f"  date range: {oldest} → {newest} ({span_days} days)")
        sample = wide_rows[0]
        populated = {k: v for k, v in sample.items() if v not in (None, "", 0)}
        print(f"  sample keys with values: {sorted(populated.keys())}")

    if len(wide_rows) > 4:
        wide_verdict = "deep — per-symbol queries return multi-year history"
    elif len(wide_rows) >= 2:
        wide_verdict = "moderate — a few quarters available"
    else:
        wide_verdict = "shallow — effectively near-term only"
    print(f"\nwide-window verdict: {wide_verdict}")

    if not args.walk:
        print(
            "\nNext: re-run with --walk to test whether narrow historical "
            "windows unlock past quarters (the deciding experiment for "
            "full historical backfill on free tier)."
        )
        return 0

    # Test 2: walk 90-day windows backward. If Finnhub returns events
    # within each window even though it returns 1 for the wide query,
    # window-walking is a viable backfill strategy.
    print(f"\n--- walking 90-day windows backward for {symbol} ---")
    quarters_back = args.years * 4
    found_via_walk: list[tuple[str, str]] = []
    seen_dates: set[str] = set(r.get("date", "")[:10] for r in wide_rows)
    for q in range(1, quarters_back + 1):
        window_end = today - timedelta(days=(q - 1) * 91)
        window_start = window_end - timedelta(days=91)
        try:
            rows = fetch(token, symbol, window_start, window_end)
        except requests.HTTPError as exc:
            print(f"  Q-{q} {window_start}→{window_end}: HTTP error {exc}")
            break
        new_events = [r for r in rows if r.get("date", "")[:10] not in seen_dates]
        seen_dates.update(r.get("date", "")[:10] for r in rows)
        if new_events:
            for r in new_events:
                found_via_walk.append((r.get("date", "?"), r.get("quarter", "?")))
            print(
                f"  Q-{q} {window_start}→{window_end}: {len(rows)} events, "
                f"{len(new_events)} NEW (e.g. {new_events[0].get('date')} "
                f"Q{new_events[0].get('quarter')} eps={new_events[0].get('epsActual')})"
            )
        else:
            print(f"  Q-{q} {window_start}→{window_end}: {len(rows)} events, 0 new")

    print(f"\nWalk discovered {len(found_via_walk)} additional historical events")
    if len(found_via_walk) >= quarters_back // 2:
        walk_verdict = (
            "WALKING WORKS — full historical backfill is viable. "
            "Add a window-walking mode to sync_finnhub_earnings.py and run on weekends."
        )
    elif len(found_via_walk) > 0:
        walk_verdict = (
            "WALKING PARTIAL — some past quarters retrievable. "
            "Worth a weekly Sunday sweep across the full universe."
        )
    else:
        walk_verdict = (
            "WALKING DOESN'T HELP — free tier truly returns only the most "
            "recent event regardless of window. Accumulate forward over time."
        )
    print(f"\nwalk verdict: {walk_verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
