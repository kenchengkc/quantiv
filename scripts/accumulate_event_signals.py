#!/usr/bin/env python3
"""Forward-accumulate pre-earnings event signals for upcoming reporters.

The high-signal event data (put/call & options VOI, short interest) has no free
*historical* feed, but Massive serves it cheaply for the *current* snapshot. So
we snapshot it for every UPCOMING reporter each day and append to an append-only
JSONL panel — over ~4-8 quarters this matures into a training panel of
pre-earnings signals keyed to each event, which the ML feature pipeline can then
join (nearest snapshot before the print) and paired-test.

Unlike `sync_provider_enrichments` (which enriches the most *popular* symbols and
so rarely catches a name near its print), this targets the calendar's upcoming
reporters directly. CI is stateless, so the panel lives in git and is appended
daily (`git add -f data/event_signals_panel.jsonl`).

Usage:
  python scripts/accumulate_event_signals.py --lead-days 12 --max-symbols 80
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

REPO = Path(__file__).resolve().parent.parent
PUBLIC_WEEKS = REPO / "apps" / "frontend" / "public" / "weeks"
PANEL = REPO / "data" / "event_signals_panel.jsonl"
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
    """(symbol, earnings_date, timing) for events between today and +lead_days,
    deduped to the nearest upcoming print per symbol."""
    horizon = today + timedelta(days=lead_days)
    best: dict[str, dict] = {}
    for path in sorted(PUBLIC_WEEKS.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            events = json.loads(path.read_text()).get("events", [])
        except (OSError, json.JSONDecodeError):
            continue
        for e in events:
            sym = (e.get("ticker") or "").upper()
            ed = (e.get("earnings_date") or "")[:10]
            if not sym or not ed:
                continue
            try:
                edd = date.fromisoformat(ed)
            except ValueError:
                continue
            if today <= edd <= horizon:
                prev = best.get(sym)
                if prev is None or edd < date.fromisoformat(prev["earnings_date"]):
                    best[sym] = {"act_symbol": sym, "earnings_date": ed,
                                 "timing": (e.get("timing") or "").lower()}
    return list(best.values())


def fetch_options_signal(sym: str, key: str, base: str, sess: requests.Session) -> dict | None:
    try:
        r = sess.get(f"{base}/v3/snapshot/options/{sym}",
                     params={"limit": 250, "apikey": key}, timeout=30)
        results = r.json().get("results", [])
    except Exception:
        return None
    if not results:
        return None
    coi = poi = cvol = pvol = 0
    atm_ivs: list[float] = []
    for c in results:
        det = c.get("details") or {}
        oi = c.get("open_interest") or 0
        vol = (c.get("day") or {}).get("volume") or 0
        is_call = det.get("contract_type") == "call"
        if is_call:
            coi += oi
            cvol += vol
        else:
            poi += oi
            pvol += vol
        delta = (c.get("greeks") or {}).get("delta")
        iv = c.get("implied_volatility")
        if iv and delta is not None and 0.35 <= abs(delta) <= 0.65:
            atm_ivs.append(float(iv))
    tot_oi, tot_vol = coi + poi, cvol + pvol
    return {
        "put_call_oi_ratio": round(poi / coi, 6) if coi else None,
        "put_call_vol_ratio": round(pvol / cvol, 6) if cvol else None,
        "options_voi": round(tot_vol / tot_oi, 6) if tot_oi else None,
        "total_call_oi": coi, "total_put_oi": poi,
        "total_call_vol": cvol, "total_put_vol": pvol,
        "atm_iv_snap": round(median(atm_ivs), 6) if atm_ivs else None,
    }


def fetch_short_interest(sym: str, key: str, base: str, sess: requests.Session) -> dict:
    try:
        r = sess.get(f"{base}/stocks/v1/short-interest",
                     params={"ticker": sym, "limit": 1, "sort": "settlement_date.desc",
                             "apikey": key}, timeout=30)
        row = (r.json().get("results") or [{}])[0]
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
                r = json.loads(line)
                keys.add((r["snapshot_date"], r["act_symbol"], r["earnings_date"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lead-days", type=int, default=12,
                    help="snapshot reporters whose print is within N days")
    ap.add_argument("--max-symbols", type=int, default=80)
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    _load_env()
    key, base = _keys()
    today = datetime.now(ET).date()
    snapshot_date = today.isoformat()

    reporters = upcoming_reporters(today, args.lead_days)
    existing = load_existing_keys()
    todo = [r for r in reporters
            if (snapshot_date, r["act_symbol"], r["earnings_date"]) not in existing][: args.max_symbols]
    print(f"{len(reporters)} upcoming reporters (≤{args.lead_days}d); "
          f"{len(todo)} to snapshot today ({snapshot_date}).")
    if not todo:
        return 0

    sess = requests.Session()
    written = 0
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    with PANEL.open("a") as fh:
        for i, rep in enumerate(todo, 1):
            sym = rep["act_symbol"]
            opt = fetch_options_signal(sym, key, base, sess)
            if opt is None:
                continue  # no chain → not optionable / no data; skip
            si = fetch_short_interest(sym, key, base, sess)
            row = {
                "snapshot_date": snapshot_date,
                "act_symbol": sym,
                "earnings_date": rep["earnings_date"],
                "timing": rep["timing"],
                "lead_days": (date.fromisoformat(rep["earnings_date"]) - today).days,
                **opt, **si,
            }
            fh.write(json.dumps(row) + "\n")
            written += 1
            if args.sleep:
                time.sleep(args.sleep)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} …", flush=True)

    total = sum(1 for _ in PANEL.open()) if PANEL.exists() else 0
    print(f"wrote {written} rows; panel now {total} rows → {PANEL.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
