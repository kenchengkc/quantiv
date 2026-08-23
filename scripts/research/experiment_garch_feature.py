#!/usr/bin/env python3
"""Does a GARCH/EWMA conditional-vol feature buy point accuracy?

GARCH(1,1) one-step-ahead vol with the usual (α≈0.06, β≈0.92) is, to first
order, a RiskMetrics EWMA of squared daily log-returns (λ≈0.94). We compute that
EWMA daily vol per ticker as-of each earnings snapshot (leakage-safe: only
returns strictly before the snapshot), annualize it, and paired-test the L1
model with vs without it — same folds, seeds, and decay weights as
experiment_model_improvements. Prior is null: the model already carries
cc_rv_10d/20d, parkinson_rv_10/20/60d, vol_of_vol_20d, hv_current, iv_current.

Usage: python scripts/research/experiment_garch_feature.py --ml-dir /tmp/ml_garch [--oos-offset 150]
"""
from __future__ import annotations

import argparse
import glob
import sys
from math import sqrt
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

sys.path.insert(0, str(Path(__file__).resolve().parent))
from experiment_model_improvements import BASE, decay_w, make_folds  # noqa: E402
from walk_forward import feature_target_split, load_training_frame  # noqa: E402

LAMBDA = 0.94  # RiskMetrics daily decay ≈ GARCH(1,1) persistence
MIN_OBS = 20   # need a minimal return history for a stable EWMA


def ewma_vol_panel(lam: float = LAMBDA) -> pd.DataFrame:
    """Per (symbol, date) annualized EWMA vol of daily log returns, shifted so a
    given date's value uses only returns up to the PRIOR close (no look-ahead)."""
    g = sorted(glob.glob("data/parquet/ohlcv/**/*.parquet", recursive=True))
    con = duckdb.connect()
    px = con.execute(
        f"""
        SELECT act_symbol, CAST(date AS DATE) AS date, close
        FROM read_parquet({g!r})
        WHERE close > 0
        ORDER BY act_symbol, date
        """
    ).fetchdf()
    px["ret"] = np.log(px.groupby("act_symbol")["close"].transform(lambda s: s / s.shift(1)))
    px = px.dropna(subset=["ret"])
    # EWMA variance of returns; shift(1) so the value at row t excludes today's
    # return (which for a BMO/AMC snapshot the model would not yet know).
    def _vol(s: pd.Series) -> pd.Series:
        var = s.pow(2).ewm(alpha=1 - lam, adjust=False).mean()
        return np.sqrt(var) * np.sqrt(252.0)
    px["ewma_vol"] = px.groupby("act_symbol")["ret"].transform(_vol)
    px["ewma_vol"] = px.groupby("act_symbol")["ewma_vol"].shift(1)
    cnt = px.groupby("act_symbol").cumcount()
    px.loc[cnt < MIN_OBS, "ewma_vol"] = np.nan
    return px[["act_symbol", "date", "ewma_vol"]]


def attach_ewma(df: pd.DataFrame, panel: pd.DataFrame) -> pd.Series:
    """As-of join: each snapshot gets the latest EWMA vol on/before its date."""
    left = df[["__symbol", "__earnings_date"]].copy()
    left["__earnings_date"] = pd.to_datetime(left["__earnings_date"]).astype("datetime64[ns]")
    left = left.reset_index().rename(columns={"index": "_ord"})
    left = left.sort_values("__earnings_date")
    pan = panel.copy()
    pan["date"] = pd.to_datetime(pan["date"]).astype("datetime64[ns]")
    pan = pan.sort_values("date")
    merged = pd.merge_asof(
        left, pan,
        left_on="__earnings_date", right_on="date",
        left_by="__symbol", right_by="act_symbol",
        direction="backward",
    )
    return merged.sort_values("_ord")["ewma_vol"].reset_index(drop=True)


def run(horizon, ml_dir, seeds, n_folds, test_days, hl, min_train, offset, panel):
    df = load_training_frame(ml_dir, horizon)
    X, y, ed = feature_target_split(df)
    ewma = attach_ewma(df, panel)
    ed = pd.to_datetime(ed)
    folds = make_folds(ed, n_folds, test_days, offset)
    l1 = {"objective": "regression_l1"}

    base_d, dl = [], []
    cov = float(ewma.notna().mean())
    for (c0, c1) in folds:
        tr = (ed < c0).to_numpy()
        te = ((ed >= c0) & (ed < c1)).to_numpy()
        if te.sum() < 30 or tr.sum() < min_train:
            continue
        w = decay_w(ed[tr], c0, hl)
        Xtr, Xte = X[tr], X[te]
        ytr, yte = y[tr].to_numpy(), y[te].to_numpy()
        Xtr_g = Xtr.assign(ewma_vol=ewma[tr].to_numpy())
        Xte_g = Xte.assign(ewma_vol=ewma[te].to_numpy())
        for seed in seeds:
            mb = LGBMRegressor(**{**BASE, "random_state": seed, **l1})
            mb.fit(Xtr, ytr, sample_weight=w)
            base = mean_absolute_error(yte, mb.predict(Xte))
            mg = LGBMRegressor(**{**BASE, "random_state": seed, **l1})
            mg.fit(Xtr_g, ytr, sample_weight=w)
            g = mean_absolute_error(yte, mg.predict(Xte_g))
            base_d.append(base)
            dl.append(g - base)
    return np.array(base_d), np.array(dl), cov


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ml-dir", default="/tmp/ml_garch")
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 2, 7])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--test-days", type=int, default=30)
    ap.add_argument("--half-life", type=float, default=0.5)
    ap.add_argument("--min-train", type=int, default=2000)
    ap.add_argument("--oos-offset", type=int, default=0)
    args = ap.parse_args()

    print("Building EWMA(λ=0.94) vol panel from OHLCV …", flush=True)
    panel = ewma_vol_panel()
    print(f"panel rows={len(panel):,}\n")

    print(f"GARCH/EWMA feature test  horizons={args.horizons}  oos_offset={args.oos_offset}d")
    print(f"{'T':>3} {'base MAE':>10} {'Δ vs base':>10} {'±std':>8} {'t':>6} {'cov':>6}  verdict")
    print("-" * 70)
    for h in args.horizons:
        base_d, dl, cov = run(
            h, Path(args.ml_dir), args.seeds, args.folds, args.test_days,
            args.half_life, args.min_train, args.oos_offset, panel,
        )
        mae = float(base_d.mean())
        dmean = float(dl.mean())
        dstd = float(dl.std(ddof=1)) if len(dl) > 1 else 0.0
        t = dmean / (dstd / sqrt(len(dl))) if dstd > 0 else 0.0
        pct = dmean / mae * 100 if mae else 0.0
        if dmean < 0 and abs(t) >= 2:
            verdict = "✅ better (sig)"
        elif dmean < 0:
            verdict = "~ better (noisy)"
        elif abs(t) >= 2:
            verdict = "❌ worse (sig)"
        else:
            verdict = "~ worse (noisy)"
        print(f"{h:>3} {mae:>10.4f} {pct:>+9.2f}% {dstd:>8.4f} {t:>6.1f} {cov:>5.0%}  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
