#!/usr/bin/env python3
"""Quick directional read: does pre-earnings SHORT INTEREST relate to the move?

put/call volume & options VOI have no free stored history (the options_chain
parquet carries IV/greeks but no volume/OI), so they can only be judged once the
forward panel (accumulate_event_signals.py) matures. Short interest, however, has
~15 months of free Massive history — enough to test now whether days-to-cover (a
crowded-borrow proxy) relates to the realized earnings move in magnitude or
direction. This is an effectiveness probe, NOT a model change: it reports rank
correlations + a decile table so we know if the signal is worth wiring later.

Usage: python scripts/research/probe_signal_effectiveness.py --n 700
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]  # scripts/research/ → repo root


def _load_env():
    for f in (REPO / "config" / ".env.local",):
        if f.exists():
            for line in f.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def sample_events(n: int, seed: int) -> pd.DataFrame:
    """Historical events with a signed realized move, within the short-interest
    history window (~2025-02 onward)."""
    con = duckdb.connect(str(REPO / "data" / "quantiv.duckdb"), read_only=True)
    return con.execute(
        f"""
        WITH ev AS (
            SELECT act_symbol, date AS earnings_date, LOWER(timing) AS timing
            FROM v_earnings WHERE date BETWEEN DATE '2025-02-20' AND DATE '2026-05-20'
        ),
        pre AS (
            SELECT e.act_symbol, e.earnings_date, e.timing, o.close AS pre_close, o.date AS pre_date,
                   ROW_NUMBER() OVER (PARTITION BY e.act_symbol, e.earnings_date ORDER BY o.date DESC) rn
            FROM ev e JOIN v_ohlcv o ON o.act_symbol=e.act_symbol AND o.close>0
              AND o.date <= CASE WHEN e.timing LIKE '%after%' THEN e.earnings_date ELSE e.earnings_date - INTERVAL '1' DAY END
        ),
        post AS (
            SELECT e.act_symbol, e.earnings_date, o.close AS post_close,
                   ROW_NUMBER() OVER (PARTITION BY e.act_symbol, e.earnings_date ORDER BY o.date ASC) rn
            FROM ev e JOIN v_ohlcv o ON o.act_symbol=e.act_symbol AND o.close>0
              AND o.date >= CASE WHEN e.timing LIKE '%after%' THEN e.earnings_date + INTERVAL '1' DAY ELSE e.earnings_date END
        )
        SELECT pre.act_symbol AS symbol, pre.earnings_date, pre.pre_date AS snap_date,
               post.post_close/pre.pre_close - 1.0 AS realized
        FROM pre JOIN post USING (act_symbol, earnings_date)
        WHERE pre.rn=1 AND post.rn=1 AND pre.pre_close>0 AND post.post_close>0
        ORDER BY hash(pre.act_symbol || pre.earnings_date::VARCHAR || '{seed}')
        LIMIT {n}
        """
    ).fetchdf()


def short_history(sym: str, key: str, base: str, sess: requests.Session) -> pd.DataFrame:
    try:
        r = sess.get(f"{base}/stocks/v1/short-interest",
                     params={"ticker": sym, "limit": 40, "sort": "settlement_date.desc",
                             "apikey": key}, timeout=30)
        rows = r.json().get("results", [])
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["settlement_date"] = pd.to_datetime(df["settlement_date"])
    return df.sort_values("settlement_date")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=700)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    _load_env()
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    import provider_specs as ps  # noqa: E402
    import provider_utils as pu  # noqa: E402
    key, base = pu.massive_api_key(), ps.MASSIVE_BASE_URL

    ev = sample_events(args.n, args.seed)
    ev["snap_date"] = pd.to_datetime(ev["snap_date"])
    print(f"sampled {len(ev)} events ({ev['symbol'].nunique()} symbols); fetching short interest …")

    sess = requests.Session()
    cache: dict[str, pd.DataFrame] = {}
    recs = []
    for i, (sym, grp) in enumerate(ev.groupby("symbol"), 1):
        hist = cache.get(sym)
        if hist is None:
            hist = short_history(sym, key, base, sess)
            cache[sym] = hist
            time.sleep(0.03)
        if hist.empty:
            continue
        for _, e in grp.iterrows():
            before = hist[hist["settlement_date"] <= e["snap_date"]]
            if before.empty:
                continue
            si = before.iloc[-1]
            recs.append({
                "realized": e["realized"], "abs_move": abs(e["realized"]),
                "days_to_cover": si.get("days_to_cover"),
                "short_interest": si.get("short_interest"),
            })
        if i % 100 == 0:
            print(f"  {i} symbols …", flush=True)

    r = pd.DataFrame(recs).dropna(subset=["days_to_cover"])
    if len(r) < 30:
        print(f"only {len(r)} matched events — insufficient.")
        return 1

    from scipy.stats import spearmanr
    dtc = r["days_to_cover"].to_numpy(float)
    print(f"\nmatched events: {len(r)}   median DTC={np.median(dtc):.2f}")
    print("── rank correlations (Spearman) ──")
    for name, y in [("|realized move| (magnitude)", r["abs_move"]),
                    ("signed realized move (direction)", r["realized"])]:
        rho, p = spearmanr(dtc, y)
        sig = "✅ signal" if p < 0.05 else "— null"
        print(f"  DTC vs {name:<34} rho={rho:+.3f}  p={p:.3f}  {sig}")

    # Decile of days-to-cover → mean |move| and mean signed move
    r = r.copy()
    r["dtc_decile"] = pd.qcut(dtc, 5, labels=False, duplicates="drop")
    tab = r.groupby("dtc_decile").agg(
        n=("realized", "size"), dtc=("days_to_cover", "mean"),
        abs_move=("abs_move", "mean"), signed=("realized", "mean")).reset_index()
    print("\n── quintile by days-to-cover ──")
    print(f"  {'q':>2} {'n':>4} {'DTC':>6} {'mean|move|':>11} {'mean signed':>12}")
    for _, t in tab.iterrows():
        print(f"  {int(t['dtc_decile']):>2} {int(t['n']):>4} {t['dtc']:>6.2f} "
              f"{t['abs_move']:>10.2%} {t['signed']:>+11.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
