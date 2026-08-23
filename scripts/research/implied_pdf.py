#!/usr/bin/env python3
"""Implied probability density → asymmetric earnings-move bands (Breeden-Litzenberger).

The production EM band is symmetric (±straddle%). But the option smile carries
the market's full *risk-neutral density* (RND) over the post-earnings price, and
it is skewed and fat-tailed — downside crashes are priced richer than upside pops.
This module extracts that density and turns it into directional up/down bands:

  1. Build the IV smile from the OTM side of the chain (puts below forward, calls
     above) — deep-ITM quotes are stale/noisy, so we never use them.
  2. Smooth IV in log-moneyness (spline), flat-extrapolate the wings.
  3. Reconstruct call prices C(K) on a fine grid via Black-Scholes, then
     RND g(K) = e^{rT} · ∂²C/∂K²  (Breeden-Litzenberger, 1978). Clip tiny
     numerical negatives and renormalize to integrate to 1.
  4. The RND is risk-neutral, so it OVER-states realized dispersion (variance-risk
     premium; realized ≈ 0.49 × straddle). De-bias by shrinking deviations from
     the center by a calibrated ratio k — this narrows the band while *preserving*
     the smile's skew/kurtosis, giving asymmetric up/down moves.

Risk-neutral quantiles are exact-ish for option *pricing*; the de-biased ones are
the physical bands we surface. `validate` measures empirical coverage (does the
80 % band contain the realized move ~80 % of the time?) and pinball loss against
the symmetric ±straddle baseline.

═══════════════════════════════════════════════════════════════════════════════
EMPIRICAL VERDICT (650 earnings events, 2023-07 → 2026-04, paired):
  • The production ±straddle band is ~2× too WIDE — it covers 0.97 of realized
    moves inside the nominal 80 % band. Narrowing the implied move by k≈0.45
    (≈ the measured 0.49 realized/straddle ratio) calibrates the 80 % band to
    0.80 and cuts pinball loss ~21 %. THIS is the real, shippable win.
  • The asymmetric B-L *shape* does NOT help: at every de-bias k the skewed RND
    bands LOSE to a symmetric Gaussian narrowed by the same k (−16 % … −44 %
    pinball). With ~15-27 strikes/expiry the extracted skew/kurt is noise, and
    the monthly-expiry RND is the ~30-day terminal density, not the 1-2 day
    event move. So: ship the symmetric de-bias, NOT the asymmetry.
  • The 80 % and 50 % bands don't calibrate at one k → realized moves are
    fat-tailed; a symmetric Student-t band would calibrate both (cheap follow-up).
This module is the research artifact that measured the de-bias and disproved the
asymmetry hypothesis; it is intentionally NOT wired into the UI.
═══════════════════════════════════════════════════════════════════════════════

Usage:
  python scripts/research/implied_pdf.py demo --symbol AAPL
  python scripts/research/implied_pdf.py validate --n 900 --center forward
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date as Date
from typing import Optional

import numpy as np
import pandas as pd
from scipy.interpolate import UnivariateSpline
from scipy.stats import norm

CHAIN_GLOB = "data/parquet/options_chain/year=*/**/*.parquet"
DUCKDB_PATH = "data/quantiv.duckdb"

# Quantiles surfaced as bands. p50 is the RND median (≈ directional drift).
BAND_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


# ───────────────────────── Black-Scholes ─────────────────────────
def bs_call(F: float, K: np.ndarray, T: float, sigma: np.ndarray, r: float = 0.0) -> np.ndarray:
    """Undiscounted-forward Black-76 call price (r folded out via forward F)."""
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    sig_sqrt = np.maximum(sigma, 1e-6) * np.sqrt(max(T, 1e-9))
    d1 = (np.log(F / K) + 0.5 * sig_sqrt**2) / sig_sqrt
    d2 = d1 - sig_sqrt
    disc = np.exp(-r * T)
    return disc * (F * norm.cdf(d1) - K * norm.cdf(d2))


# ───────────────────────── data classes ─────────────────────────
@dataclass
class ImpliedPDF:
    symbol: str
    date: Date
    expiration: Date
    forward: float
    T: float
    K_grid: np.ndarray
    rnd: np.ndarray              # risk-neutral density over terminal price
    rn_quantiles: dict           # p -> terminal price (risk-neutral)
    rn_move: dict                # p -> return vs forward (risk-neutral)
    phys_move: dict              # p -> return vs forward (de-biased / physical)
    skew: float
    kurtosis: float
    straddle_pct: float          # symmetric ATM straddle implied move
    debias: float
    n_strikes: int
    diagnostics: dict = field(default_factory=dict)


# ───────────────────────── smile + RND ─────────────────────────
def _otm_smile(chain: pd.DataFrame, forward: float) -> pd.DataFrame:
    """One IV per strike from the OTM side (puts<F, calls≥F); drop sentinels."""
    chain = chain.copy()
    chain["mid"] = (chain["bid"] + chain["ask"]) / 2.0
    good = (chain["vol"] > 0.01) & (chain["vol"] < 3.0) & (chain["ask"] > 0)
    chain = chain[good]
    puts = chain[(chain["call_put"] == "Put") & (chain["strike"] < forward)]
    calls = chain[(chain["call_put"] == "Call") & (chain["strike"] >= forward)]
    smile = pd.concat([puts, calls])[["strike", "vol"]].dropna()
    smile = smile.groupby("strike", as_index=False)["vol"].mean().sort_values("strike")
    return smile


def _infer_forward(chain: pd.DataFrame) -> Optional[float]:
    """Put-call parity: F = K + (C_mid − P_mid), averaged over the most ATM
    strikes where both legs quote. r≈0 over the short earnings horizon."""
    c = chain[chain["call_put"] == "Call"].set_index("strike")
    p = chain[chain["call_put"] == "Put"].set_index("strike")
    common = c.index.intersection(p.index)
    if len(common) == 0:
        return None
    cmid = (c.loc[common, "bid"] + c.loc[common, "ask"]) / 2.0
    pmid = (p.loc[common, "bid"] + p.loc[common, "ask"]) / 2.0
    parity_f = common.to_numpy(float) + (cmid - pmid).to_numpy(float)
    # weight toward strikes where |C−P| is small (closest to ATM, most reliable)
    diff = np.abs((cmid - pmid).to_numpy(float))
    order = np.argsort(diff)[: max(3, len(diff) // 4)]
    return float(np.median(parity_f[order]))


def build_rnd(chain: pd.DataFrame, T: float, r: float = 0.0,
              n_grid: int = 600) -> Optional[dict]:
    """Smile → smoothed IV(K) → BS call curve → Breeden-Litzenberger RND."""
    forward = _infer_forward(chain)
    if not forward or forward <= 0:
        return None
    smile = _otm_smile(chain, forward)
    if len(smile) < 6:
        return None

    K = smile["strike"].to_numpy(float)
    iv = smile["vol"].to_numpy(float)
    x = np.log(K / forward)  # log-moneyness

    # Smoothing spline in log-moneyness. s scales with noise×n so we smooth the
    # quote jitter without ironing out real skew; clamp spline degree for few pts.
    order = np.argsort(x)
    x, iv = x[order], iv[order]
    x_u, idx = np.unique(x, return_index=True)
    iv_u = iv[idx]
    if len(x_u) < 6:
        return None
    k_deg = min(3, len(x_u) - 1)
    try:
        spl = UnivariateSpline(x_u, iv_u, k=k_deg, s=len(x_u) * 0.0004)
    except Exception:
        return None

    # Fine grid spanning the observed strikes; flat-extrapolate IV past the wings
    # (a spline left to roam outside its data explodes and poisons the 2nd deriv).
    x_lo, x_hi = x_u.min(), x_u.max()
    xg = np.linspace(x_lo, x_hi, n_grid)
    Kg = forward * np.exp(xg)
    ivg = spl(xg)
    ivg = np.clip(ivg, 0.02, 3.0)

    C = bs_call(forward, Kg, T, ivg, r)
    # Breeden-Litzenberger: g(K) = e^{rT} ∂²C/∂K²
    dK = np.gradient(Kg)
    dC = np.gradient(C, Kg)
    d2C = np.gradient(dC, Kg)
    g = np.exp(r * T) * d2C
    g = np.clip(g, 0.0, None)
    area = np.trapz(g, Kg)
    if not np.isfinite(area) or area <= 0:
        return None
    g = g / area
    return {"forward": forward, "K_grid": Kg, "rnd": g, "iv_grid": ivg,
            "atm_iv": float(spl(0.0))}


def _quantiles(K: np.ndarray, g: np.ndarray, ps) -> dict:
    cdf = np.concatenate([[0.0], np.cumsum((g[:-1] + g[1:]) / 2.0 * np.diff(K))])
    cdf = cdf / cdf[-1]
    return {p: float(np.interp(p, cdf, K)) for p in ps}


def _moments(K: np.ndarray, g: np.ndarray, forward: float):
    """Skew & excess kurtosis of the RND return distribution."""
    r = K / forward - 1.0
    w = g * np.gradient(K)
    w = w / w.sum()
    m = np.sum(w * r)
    var = np.sum(w * (r - m) ** 2)
    sd = np.sqrt(max(var, 1e-12))
    skew = np.sum(w * ((r - m) / sd) ** 3)
    kurt = np.sum(w * ((r - m) / sd) ** 4) - 3.0
    return float(skew), float(kurt)


def implied_pdf_bands(chain: pd.DataFrame, the_date: Date, expiration: Date,
                      symbol: str = "", debias: float = 1.0,
                      r: float = 0.0) -> Optional[ImpliedPDF]:
    """Full pipeline for one (symbol, date, expiry) chain → asymmetric bands."""
    the_date = pd.Timestamp(the_date).date()
    expiration = pd.Timestamp(expiration).date()
    T = max((expiration - the_date).days, 1) / 365.0
    out = build_rnd(chain, T, r)
    if out is None:
        return None
    F, Kg, g = out["forward"], out["K_grid"], out["rnd"]

    rn_q = _quantiles(Kg, g, BAND_QUANTILES)
    rn_move = {p: rn_q[p] / F - 1.0 for p in BAND_QUANTILES}
    center = rn_move[0.50]
    # De-bias: shrink deviations from the RN median by `debias`, preserving shape.
    phys_move = {p: center + debias * (rn_move[p] - center) for p in BAND_QUANTILES}
    skew, kurt = _moments(Kg, g, F)
    atm_iv = out["atm_iv"]
    straddle_pct = float(atm_iv * np.sqrt(T) * np.sqrt(2.0 / np.pi))  # E|move| under BS

    return ImpliedPDF(
        symbol=symbol, date=the_date, expiration=expiration, forward=F, T=T,
        K_grid=Kg, rnd=g, rn_quantiles=rn_q, rn_move=rn_move, phys_move=phys_move,
        skew=skew, kurtosis=kurt, straddle_pct=straddle_pct, debias=debias,
        n_strikes=int(out.get("n_strikes", len(_otm_smile(chain, F)))),
        diagnostics={"atm_iv": atm_iv},
    )


# ───────────────────────── data access ─────────────────────────
def load_chain(con, symbol: str, the_date: Date, expiration: Date) -> pd.DataFrame:
    return con.execute(
        f"""
        SELECT strike, call_put, bid, ask, vol, delta
        FROM read_parquet('{CHAIN_GLOB}')
        WHERE act_symbol = ? AND date = ?::DATE AND expiration = ?::DATE
        """,
        [symbol, str(the_date), str(expiration)],
    ).fetchdf()


def pick_expiry(con, symbol: str, the_date: Date, after: Date) -> Optional[Date]:
    """First expiry strictly after `after` (the earnings date) that has a chain
    snapshot on `the_date` with enough strikes to fit a smile."""
    row = con.execute(
        f"""
        SELECT expiration, COUNT(DISTINCT strike) ns
        FROM read_parquet('{CHAIN_GLOB}')
        WHERE act_symbol = ? AND date = ?::DATE AND expiration > ?::DATE
        GROUP BY expiration HAVING ns >= 8
        ORDER BY expiration ASC LIMIT 1
        """,
        [symbol, str(the_date), str(after)],
    ).fetchone()
    return row[0] if row else None


# ───────────────────────── validation ─────────────────────────
def _validation_events(con, n: int, seed: int = 0) -> pd.DataFrame:
    """Earnings events with a realized close-to-close move AND a usable
    pre-earnings chain. Snapshot = last session before the earnings date."""
    return con.execute(
        f"""
        WITH ev AS (
            SELECT act_symbol, date AS earnings_date, LOWER(timing) AS timing
            FROM v_earnings
            WHERE date BETWEEN DATE '2023-07-01' AND DATE '2026-04-30'
        ),
        snap AS (  -- last chain date strictly before earnings
            SELECT e.act_symbol, e.earnings_date, e.timing, MAX(oc.date) AS snap_date
            FROM ev e
            JOIN read_parquet('{CHAIN_GLOB}') oc
              ON oc.act_symbol = e.act_symbol
             AND oc.date < e.earnings_date
             AND oc.date >= e.earnings_date - INTERVAL '5' DAY
            GROUP BY 1,2,3
        ),
        pre AS (
            SELECT s.*, o.close AS pre_close,
                   ROW_NUMBER() OVER (PARTITION BY s.act_symbol, s.earnings_date
                                      ORDER BY o.date DESC) rn
            FROM snap s JOIN v_ohlcv o
              ON o.act_symbol = s.act_symbol AND o.date <= s.snap_date AND o.close > 0
        ),
        post AS (
            SELECT s.act_symbol, s.earnings_date, o.close AS post_close,
                   ROW_NUMBER() OVER (PARTITION BY s.act_symbol, s.earnings_date
                                      ORDER BY o.date ASC) rn
            FROM snap s JOIN v_ohlcv o
              ON o.act_symbol = s.act_symbol
             AND o.date >= CASE WHEN s.timing LIKE '%after%'
                                THEN s.earnings_date + INTERVAL '1' DAY
                                ELSE s.earnings_date END
             AND o.close > 0
        )
        SELECT pre.act_symbol AS symbol, pre.earnings_date, pre.timing,
               pre.snap_date, pre.pre_close, post.post_close,
               post.post_close / pre.pre_close - 1.0 AS realized_move
        FROM pre JOIN post
          ON pre.act_symbol = post.act_symbol AND pre.earnings_date = post.earnings_date
        WHERE pre.rn = 1 AND post.rn = 1
          AND pre.pre_close > 0 AND post.post_close > 0
        -- Deterministic pseudo-random subsample; USING SAMPLE collapses to 0
        -- rows when combined with the window-function CTEs above.
        ORDER BY hash(pre.act_symbol || pre.earnings_date::VARCHAR || '{seed}')
        LIMIT {n}
        """,
    ).fetchdf()


def _pinball(y, q, a):
    d = np.asarray(y) - np.asarray(q)
    return float(np.mean(np.where(d >= 0, a * d, (a - 1) * d)))


def validate(n: int, debias: float, seed: int = 0, center: str = "median") -> int:
    import duckdb
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    ev = _validation_events(con, n, seed)
    print(f"Sampled {len(ev)} earnings events; loading chains + fitting RNDs …\n")

    rows = []
    for _, e in ev.iterrows():
        sym, snap, earn = e["symbol"], e["snap_date"], e["earnings_date"]
        exp = pick_expiry(con, sym, snap, earn)
        if exp is None:
            continue
        chain = load_chain(con, sym, snap, exp)
        if len(chain) < 12:
            continue
        pdf = implied_pdf_bands(chain, snap, exp, sym, debias=1.0)  # raw RN; k applied below
        if pdf is None:
            continue
        rows.append({
            "realized": e["realized_move"], "straddle": pdf.straddle_pct,
            "skew": pdf.skew, "kurt": pdf.kurtosis,
            **{f"rn{int(p*100)}": pdf.rn_move[p] for p in BAND_QUANTILES},
        })
    r = pd.DataFrame(rows)
    if r.empty:
        print("No events produced a valid RND — check chain coverage.")
        return 1

    y = r.realized.to_numpy()
    # Symmetric ±straddle baseline (production today): Gaussian quantiles around 0.
    z = {10: -1.2816, 25: -0.6745, 50: 0.0, 75: 0.6745, 90: 1.2816}
    sigma = (r.straddle / np.sqrt(2.0 / np.pi)).to_numpy()
    base_q = {p: sigma * z[p] for p in (10, 25, 50, 75, 90)}
    base_cov80 = float(((y >= base_q[10]) & (y <= base_q[90])).mean())
    base_cov50 = float(((y >= base_q[25]) & (y <= base_q[75])).mean())
    pb_base = float(np.mean([_pinball(y, base_q[p], p / 100) for p in (10, 25, 50, 75, 90)]))

    print(f"events used: {len(r)}    center={center}")
    print(f"  RND skew (mean):        {r['skew'].mean():+.3f}   (>0 upside-fat, <0 downside-fat)")
    print(f"  RND excess kurt (mean): {r['kurt'].mean():+.3f}")
    print(f"  median |RN p90 move|:   {r['rn90'].abs().median():.2%}   "
          f"(symmetric straddle p90: {np.median(base_q[90]):.2%})")
    print()
    print(f"  ── symmetric ±straddle baseline ──")
    print(f"    80% coverage: {base_cov80:.3f}   50% coverage: {base_cov50:.3f}   "
          f"pinball: {pb_base:.5f}")
    print()
    # Decompose the gain: at each de-bias k, compare the asymmetric RND bands to
    # a SYMMETRIC Gaussian narrowed by the SAME k (k·σ·z). If RND beats matched-k
    # Gaussian, the smile's skew/kurtosis is doing real work; if they tie, the
    # win is purely from narrowing and a symmetric de-biased band would suffice.
    print(f"  ── de-bias sweep: asymmetric RND vs symmetric-Gaussian, same k ──")
    print(f"  {'k':>5} | {'RND cov80':>9} {'pin(RND)':>9} | {'symN cov80':>10} "
          f"{'symN cov50':>10} {'pin(symN)':>9} {'symN vs base':>12}")
    for k in (1.0, 0.85, 0.70, 0.65, 0.60, 0.55, 0.49, 0.45, 0.40):
        c = r.rn50.to_numpy() if center == "median" else 0.0
        q = {p: c + k * (r[f"rn{p}"].to_numpy() - c) for p in (10, 25, 50, 75, 90)}
        qsym = {p: k * sigma * z[p] for p in (10, 25, 50, 75, 90)}  # matched-k Gaussian
        cov80 = float(((y >= q[10]) & (y <= q[90])).mean())
        pb = float(np.mean([_pinball(y, q[p], p / 100) for p in (10, 25, 50, 75, 90)]))
        scov80 = float(((y >= qsym[10]) & (y <= qsym[90])).mean())
        scov50 = float(((y >= qsym[25]) & (y <= qsym[75])).mean())
        pbs = float(np.mean([_pinball(y, qsym[p], p / 100) for p in (10, 25, 50, 75, 90)]))
        print(f"  {k:>5.2f} | {cov80:>9.3f} {pb:>9.5f} | {scov80:>10.3f} "
              f"{scov50:>10.3f} {pbs:>9.5f} {(1 - pbs / pb_base) * 100:>+11.1f}%")
    return 0


def demo(symbol: str, seed: int) -> int:
    import duckdb
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    ev = _validation_events(con, 400, seed)
    pref = ev[ev.symbol == symbol]
    ev = pd.concat([pref, ev[ev.symbol != symbol]]) if len(pref) else ev
    pdf = e = None
    for _, row in ev.iterrows():
        exp = pick_expiry(con, row["symbol"], row["snap_date"], row["earnings_date"])
        if exp is None:
            continue
        chain = load_chain(con, row["symbol"], row["snap_date"], exp)
        if len(chain) < 12:
            continue
        cand = implied_pdf_bands(chain, row["snap_date"], exp, row["symbol"], debias=0.55)
        if cand is not None:
            pdf, e = cand, row
            break
    if pdf is None:
        print("No valid RND found in sample.")
        return 1
    sym, snap, earn = e["symbol"], e["snap_date"], e["earnings_date"]
    print(f"{sym}  snap={snap}  earnings={earn}  expiry={pdf.expiration}  T={pdf.T:.3f}")
    print(f"forward={pdf.forward:.2f}  atm_iv={pdf.diagnostics['atm_iv']:.3f}  "
          f"skew={pdf.skew:+.3f}  kurt={pdf.kurtosis:+.3f}")
    print(f"straddle ±move: ±{pdf.straddle_pct:.2%}")
    print("risk-neutral move quantiles:")
    for p in BAND_QUANTILES:
        print(f"   p{int(p*100):02d}: rn={pdf.rn_move[p]:+.2%}   phys={pdf.phys_move[p]:+.2%}")
    print(f"realized move this event: {e['realized_move']:+.2%}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--symbol", default="AAPL")
    d.add_argument("--seed", type=int, default=0)
    v = sub.add_parser("validate")
    v.add_argument("--n", type=int, default=800)
    v.add_argument("--debias", type=float, default=0.55)
    v.add_argument("--seed", type=int, default=0)
    v.add_argument("--center", choices=["median", "forward"], default="median")
    args = ap.parse_args()
    if args.cmd == "demo":
        return demo(args.symbol, args.seed)
    return validate(args.n, args.debias, args.seed, args.center)


if __name__ == "__main__":
    raise SystemExit(main())
