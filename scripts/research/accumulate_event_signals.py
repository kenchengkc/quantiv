#!/usr/bin/env python3
"""Manually forward-accumulate pre-earnings signals for research.

The high-signal event data (put/call & options VOI, short interest) has no free
historical feed, but Massive serves it cheaply for the current snapshot. This
research helper snapshots UPCOMING reporters and appends them to an isolated
JSONL panel so a future point-in-time paired test can evaluate the signals.

The former scheduled accumulation job is retired. Running this command is an
explicit research action and never publishes a production artifact.

Usage:
  python scripts/research/accumulate_event_signals.py --lead-days 12 --max-symbols 80
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[2]
PUBLIC_WEEKS = REPO / "apps" / "frontend" / "public" / "weeks"
PANEL = REPO / "data" / "research" / "event_signals" / "panel.jsonl"
ET = ZoneInfo("America/New_York")


def _load_env() -> None:
    for f in (REPO / "config" / ".env.local", REPO / ".env.local"):
        if f.exists():
            import os

            for line in f.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _keys():
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    import provider_specs as ps  # noqa: E402
    import provider_utils as pu  # noqa: E402

    key = pu.massive_api_key()
    if not key:
        raise SystemExit("POLYGON_API_KEY/MASSIVE_API_KEY not configured")
    return key, ps.MASSIVE_BASE_URL


def upcoming_reporters(today: date, lead_days: int) -> list[dict]:
    """Return upcoming events, deduped to the nearest print per symbol."""
    horizon = today + timedelta(days=lead_days)
    best: dict[str, dict] = {}
    for path in sorted(PUBLIC_WEEKS.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            events = json.loads(path.read_text()).get("events", [])
        except (OSError, json.JSONDecodeError):
            continue
        for event in events:
            symbol = (event.get("ticker") or "").upper()
            earnings_date = (event.get("earnings_date") or "")[:10]
            if not symbol or not earnings_date:
                continue
            try:
                event_date = date.fromisoformat(earnings_date)
            except ValueError:
                continue
            if today <= event_date <= horizon:
                previous = best.get(symbol)
                if previous is None or event_date < date.fromisoformat(previous["earnings_date"]):
                    best[symbol] = {
                        "act_symbol": symbol,
                        "earnings_date": earnings_date,
                        "timing": (event.get("timing") or "").lower(),
                    }
    return list(best.values())


def fetch_options_signal(
    sym: str,
    key: str,
    base: str,
    sess: requests.Session,
) -> dict | None:
    try:
        response = sess.get(
            f"{base}/v3/snapshot/options/{sym}",
            params={"limit": 250, "apikey": key},
            timeout=30,
        )
        results = response.json().get("results", [])
    except Exception:
        return None
    if not results:
        return None
    coi = poi = cvol = pvol = 0
    atm_ivs: list[float] = []
    for contract in results:
        details = contract.get("details") or {}
        open_interest = contract.get("open_interest") or 0
        volume = (contract.get("day") or {}).get("volume") or 0
        is_call = details.get("contract_type") == "call"
        if is_call:
            coi += open_interest
            cvol += volume
        else:
            poi += open_interest
            pvol += volume
        delta = (contract.get("greeks") or {}).get("delta")
        iv = contract.get("implied_volatility")
        if iv and delta is not None and 0.35 <= abs(delta) <= 0.65:
            atm_ivs.append(float(iv))
    total_oi, total_volume = coi + poi, cvol + pvol
    return {
        "put_call_oi_ratio": round(poi / coi, 6) if coi else None,
        "put_call_vol_ratio": round(pvol / cvol, 6) if cvol else None,
        "options_voi": round(total_volume / total_oi, 6) if total_oi else None,
        "total_call_oi": coi,
        "total_put_oi": poi,
        "total_call_vol": cvol,
        "total_put_vol": pvol,
        "atm_iv_snap": round(median(atm_ivs), 6) if atm_ivs else None,
    }


def fetch_short_interest(
    sym: str,
    key: str,
    base: str,
    sess: requests.Session,
) -> dict:
    try:
        response = sess.get(
            f"{base}/stocks/v1/short-interest",
            params={
                "ticker": sym,
                "limit": 1,
                "sort": "settlement_date.desc",
                "apikey": key,
            },
            timeout=30,
        )
        row = (response.json().get("results") or [{}])[0]
    except Exception:
        return {}
    return {
        "short_days_to_cover": row.get("days_to_cover"),
        "short_interest": row.get("short_interest"),
        "short_avg_vol": row.get("avg_daily_volume"),
        "short_settlement": row.get("settlement_date"),
    }


def load_existing_keys() -> set[tuple]:
    keys = set()
    if PANEL.exists():
        for line in PANEL.read_text().splitlines():
            try:
                row = json.loads(line)
                keys.add((row["snapshot_date"], row["act_symbol"], row["earnings_date"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lead-days",
        type=int,
        default=12,
        help="snapshot reporters whose print is within N days",
    )
    parser.add_argument("--max-symbols", type=int, default=80)
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    _load_env()
    key, base = _keys()
    today = datetime.now(ET).date()
    snapshot_date = today.isoformat()

    reporters = upcoming_reporters(today, args.lead_days)
    existing = load_existing_keys()
    todo = [
        row
        for row in reporters
        if (snapshot_date, row["act_symbol"], row["earnings_date"]) not in existing
    ][: args.max_symbols]
    print(
        f"{len(reporters)} upcoming reporters (≤{args.lead_days}d); "
        f"{len(todo)} to snapshot today ({snapshot_date})."
    )
    if not todo:
        return 0

    session = requests.Session()
    written = 0
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    with PANEL.open("a") as handle:
        for index, reporter in enumerate(todo, 1):
            symbol = reporter["act_symbol"]
            options_signal = fetch_options_signal(symbol, key, base, session)
            if options_signal is None:
                continue
            short_interest = fetch_short_interest(symbol, key, base, session)
            row = {
                "snapshot_date": snapshot_date,
                "act_symbol": symbol,
                "earnings_date": reporter["earnings_date"],
                "timing": reporter["timing"],
                "lead_days": (date.fromisoformat(reporter["earnings_date"]) - today).days,
                **options_signal,
                **short_interest,
            }
            handle.write(json.dumps(row) + "\n")
            written += 1
            if args.sleep:
                time.sleep(args.sleep)
            if index % 25 == 0:
                print(f"  {index}/{len(todo)} …", flush=True)

    total = sum(1 for _ in PANEL.open()) if PANEL.exists() else 0
    print(f"wrote {written} rows; panel now {total} rows → {PANEL.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
