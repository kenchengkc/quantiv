#!/usr/bin/env python3
"""Backfill historical analyst-estimate DISPERSION from FMP (free tier).

Free-tier reality (probed): `period=quarter` is premium and `limit`≤10, but
annual estimates paginate back to ~2007, so we pull ANNUAL dispersion only:
  eps_dispersion = (epsHigh − epsLow) / |epsAvg|
  rev_dispersion = (revHigh − revLow) / |revAvg|
This is coarse (one value per fiscal year, shared by that year's 4 prints), so
it's a weak per-event signal — we backfill it to TEST whether it moves MAE at all
before paying for quarterly. Output panel: data/parquet/analyst_estimates/
dispersion_panel.parquet keyed (act_symbol, fiscal_end). Resumable: symbols in
the existing panel are skipped unless --refresh.

Usage:
  python scripts/research/backfill_analyst_dispersion.py --symbols sp500 --pages 2
  python scripts/research/backfill_analyst_dispersion.py --symbols-file syms.txt
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]  # scripts/research/ → repo root
OUT_DIR = REPO / "data" / "parquet" / "analyst_estimates"
PANEL = OUT_DIR / "dispersion_panel.parquet"
URL = "https://financialmodelingprep.com/stable/analyst-estimates"


def _load_env() -> None:
    for f in (REPO / "config" / ".env.local", REPO / ".env.local"):
        if f.exists():
            for line in f.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fmp_key() -> str:
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    import provider_utils as pu  # noqa: E402
    key = pu.fmp_api_key()
    if not key:
        raise SystemExit("FMP_API_KEY not configured")
    return key


def _disp(hi, lo, avg):
    if hi is None or lo is None or not avg:
        return None
    try:
        return (float(hi) - float(lo)) / abs(float(avg))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def fetch_symbol(sym: str, key: str, pages: int, session: requests.Session) -> list[dict]:
    rows: list[dict] = []
    for page in range(pages):
        try:
            r = session.get(URL, params={"symbol": sym, "period": "annual",
                                         "page": page, "limit": 10, "apikey": key}, timeout=30)
            data = r.json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        for d in data:
            rows.append({
                "act_symbol": sym,
                "fiscal_end": d.get("date"),
                "eps_dispersion": _disp(d.get("epsHigh"), d.get("epsLow"), d.get("epsAvg")),
                "rev_dispersion": _disp(d.get("revenueHigh"), d.get("revenueLow"), d.get("revenueAvg")),
                "num_analysts_eps": d.get("numAnalystsEps"),
            })
    return rows


def load_symbols(arg: str, file: str | None) -> list[str]:
    if file:
        return [s.strip().upper() for s in Path(file).read_text().split() if s.strip()]
    if arg == "sp500":
        data = json.loads((REPO / "lib" / "data" / "sp500-constituents.json").read_text())
        return [x["symbol"].upper() for x in data]
    if arg == "earnings":
        import duckdb
        con = duckdb.connect(str(REPO / "data" / "quantiv.duckdb"), read_only=True)
        return [r[0] for r in con.execute(
            "SELECT DISTINCT act_symbol FROM v_earnings "
            "WHERE date BETWEEN DATE '2019-06-01' AND DATE '2026-06-01' ORDER BY 1"
        ).fetchall()]
    raise SystemExit(f"unknown --symbols {arg}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="sp500", help="sp500 | earnings")
    ap.add_argument("--symbols-file", default=None)
    ap.add_argument("--pages", type=int, default=2, help="FMP pages (2 ≈ 2011-2030)")
    ap.add_argument("--refresh", action="store_true", help="refetch already-cached symbols")
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    _load_env()
    key = _fmp_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    existing = pd.read_parquet(PANEL) if PANEL.exists() else pd.DataFrame()
    done = set() if args.refresh else set(existing.get("act_symbol", pd.Series([], dtype=str)))

    symbols = [s for s in load_symbols(args.symbols, args.symbols_file) if s not in done]
    print(f"{len(symbols)} symbols to fetch ({len(done)} already cached), pages={args.pages}")

    session = requests.Session()
    new_rows: list[dict] = []
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        new_rows.extend(fetch_symbol(sym, key, args.pages, session))
        if args.sleep:
            time.sleep(args.sleep)
        if i % 50 == 0 or i == len(symbols):
            print(f"  {i}/{len(symbols)}  rows={len(new_rows)}  {time.time()-t0:.0f}s", flush=True)
            # incremental checkpoint so a mid-run abort keeps progress
            panel = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
            panel = panel.dropna(subset=["fiscal_end"]).drop_duplicates(
                ["act_symbol", "fiscal_end"], keep="last")
            panel.to_parquet(PANEL, index=False)

    panel = pd.read_parquet(PANEL)
    cov = panel["eps_dispersion"].notna().mean()
    print(f"\npanel: {len(panel)} rows, {panel['act_symbol'].nunique()} symbols, "
          f"eps_dispersion coverage {cov:.0%}")
    print(f"fiscal_end range: {panel['fiscal_end'].min()} → {panel['fiscal_end'].max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
