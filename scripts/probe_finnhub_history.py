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


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--years", type=int, default=3,
                        help="How many years back to ask for (default 3).")
    args = parser.parse_args()

    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        print("✗ FINNHUB_API_KEY missing", file=sys.stderr)
        return 1

    today = date.today()
    start = today - timedelta(days=args.years * 365)
    end = today + timedelta(days=30)

    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "symbol": args.symbol.upper(),
        "token": token,
    }
    print(f"GET {BASE_URL}?from={params['from']}&to={params['to']}&symbol={params['symbol']}")
    resp = requests.get(BASE_URL, params=params, timeout=30)
    if not resp.ok:
        print(f"✗ HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 1

    body = resp.json()
    rows = body.get("earningsCalendar") or []
    print(f"\nReturned {len(rows)} events")
    if not rows:
        print("verdict: shallow (free tier may be near-term only for this query shape)")
        return 0

    dates = sorted([r.get("date") for r in rows if r.get("date")])
    oldest, newest = dates[0], dates[-1]
    span_days = (date.fromisoformat(newest[:10]) - date.fromisoformat(oldest[:10])).days
    print(f"Date range: {oldest} → {newest} ({span_days} days)")

    # Sample row so it's obvious which fields are populated for historical
    # events (esp. epsActual / revenueActual — null on future events).
    sample = rows[0]
    populated = {k: v for k, v in sample.items() if v not in (None, "", 0)}
    print(f"Sample event keys with values: {sorted(populated.keys())}")

    if len(rows) > 4:
        verdict = "deep — per-symbol queries return multiple years of history"
    elif len(rows) >= 2:
        verdict = "moderate — a few quarters available"
    else:
        verdict = "shallow — effectively near-term only"
    print(f"\nverdict: {verdict}")
    print(
        "\nNext step: if 'deep', add a --symbols mode to sync_finnhub_earnings.py "
        "and backfill watchlist + popular names overnight. If 'shallow', keep "
        "the existing 3-call/day accumulation and let history fill in over time."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
