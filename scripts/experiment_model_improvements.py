#!/usr/bin/env python3
"""Rigorous, paired search for model improvements (not noise).

For each monthly walk-forward fold (expanding train, decay-weighted, like prod)
and each random seed, every variant is trained on the SAME (X_train, y_train, w)
and evaluated on the SAME X_test. So the variant-vs-baseline delta per (fold,seed)
is paired — it removes seed/fold variance and isolates the change. We aggregate
the paired ΔMAE across folds×seeds and report mean, std, and a t-stat; an
improvement only counts if it's consistent and clears the noise band.

Round is chosen with --round: objectives | targets | features | ensemble.
"""

from __future__ import annotations

import argparse
import sys
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walk_forward import (  # noqa: E402
    DEFAULT_PARAMS,
    feature_target_split,
    get_data_dir,
    load_training_frame,
)

BASE = dict(DEFAULT_PARAMS)
BASE.update(n_estimators=400, verbose=-1, n_jobs=-1)

ID = (lambda y: y, lambda p: p)
LOG = (lambda y: np.log1p(y), lambda p: np.clip(np.expm1(p), 0, None))


def decay_w(dates: pd.Series, cutoff: pd.Timestamp, hl: float) -> np.ndarray:
    d = (cutoff - dates).dt.days.clip(lower=0).to_numpy(dtype=float)
    return np.exp(-d / (365.0 * hl))


def lgbm(extra):
    return lambda seed: LGBMRegressor(**{**BASE, "random_state": seed, **extra})


def specs_for(round_name):
    if round_name == "objectives":
        return [
            ("baseline_L2", lgbm({}), ID, None),
            ("L1_mae", lgbm({"objective": "regression_l1"}), ID, None),
            ("huber_0.9", lgbm({"objective": "huber", "alpha": 0.9}), ID, None),
            ("fair", lgbm({"objective": "fair"}), ID, None),
            ("L2_logtarget", lgbm({}), LOG, None),
            ("L1_logtarget", lgbm({"objective": "regression_l1"}), LOG, None),
        ]
    if round_name == "validate":
        return [
            ("baseline_L2", lgbm({}), ID, None),
            ("L1_mae", lgbm({"objective": "regression_l1"}), ID, None),
            ("L1_logtarget", lgbm({"objective": "regression_l1"}), LOG, None),
        ]
    raise ValueError(round_name)


def make_folds(ed: pd.Series, n_folds: int, test_days: int, offset: int = 0):
    end = (ed.max() - pd.Timedelta(days=offset)).normalize()
    out = []
    for i in range(n_folds):
        c0 = (end - pd.Timedelta(days=test_days * (n_folds - i))).normalize()
        out.append((c0, c0 + pd.Timedelta(days=test_days)))
    return out


def run_horizon(horizon, specs, seeds, n_folds, test_days, hl, min_train, offset=0):
    df = load_training_frame(get_data_dir() / "ml_training", horizon)
    X, y, ed = feature_target_split(df)
    ed = pd.to_datetime(ed)
    folds = make_folds(ed, n_folds, test_days, offset)

    names = [s[0] for s in specs]
    maes = {n: [] for n in names}
    paired = {n: [] for n in names}  # ΔMAE vs baseline per (fold,seed)
    bias = {n: [] for n in names}    # mean(pred - actual): >0 over-predicts
    n_obs = 0
    for (c0, c1) in folds:
        tr = (ed < c0).to_numpy()
        te = ((ed >= c0) & (ed < c1)).to_numpy()
        if te.sum() < 30 or tr.sum() < min_train:
            continue
        Xtr, ytr = X[tr], y[tr].to_numpy()
        Xte, yte = X[te], y[te].to_numpy()
        w = decay_w(ed[tr], c0, hl)
        for seed in seeds:
            fold_mae = {}
            for name, fac, (fwd, inv), _feat in specs:
                m = fac(seed)
                m.fit(Xtr, fwd(ytr), sample_weight=w)
                pred = inv(m.predict(Xte))
                fold_mae[name] = mean_absolute_error(yte, pred)
                bias[name].append(float(np.mean(pred - yte)))
            base = fold_mae[names[0]]
            for name in names:
                maes[name].append(fold_mae[name])
                paired[name].append(fold_mae[name] - base)
            n_obs += 1
    return maes, paired, bias, n_obs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--round", default="objectives")
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 2, 7])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--test-days", type=int, default=30)
    ap.add_argument("--half-life", type=float, default=0.5)
    ap.add_argument("--min-train", type=int, default=2000)
    ap.add_argument("--oos-offset", type=int, default=0,
                    help="Shift the OOS window back N days (test an earlier period)")
    args = ap.parse_args()

    specs = specs_for(args.round)
    print(f"ROUND={args.round}  horizons={args.horizons}  seeds={args.seeds}  "
          f"folds={args.folds}x{args.test_days}d\n")

    for h in args.horizons:
        maes, paired, bias, n = run_horizon(
            h, specs, args.seeds, args.folds, args.test_days, args.half_life,
            args.min_train, args.oos_offset,
        )
        print(f"=== T{h}  ({n} fold×seed obs, oos_offset={args.oos_offset}d) ===")
        print(f"{'variant':>16} {'mean MAE':>10} {'Δ vs base':>10} {'±std':>8} {'t':>6} "
              f"{'bias':>9}  verdict")
        print("-" * 80)
        base_name = specs[0][0]
        for name in [s[0] for s in specs]:
            mae = float(np.mean(maes[name]))
            dl = np.array(paired[name])
            dmean, dstd = float(dl.mean()), float(dl.std(ddof=1)) if len(dl) > 1 else 0.0
            t = dmean / (dstd / sqrt(len(dl))) if dstd > 0 else 0.0
            pct = dmean / mae * 100 if mae else 0.0
            b = float(np.mean(bias[name]))
            if name == base_name:
                verdict = "(baseline)"
            elif dmean < 0 and abs(t) >= 2:
                verdict = "✅ better (sig)"
            elif dmean < 0:
                verdict = "~ better (noisy)"
            elif abs(t) >= 2:
                verdict = "❌ worse (sig)"
            else:
                verdict = "~ worse (noisy)"
            print(f"{name:>16} {mae:>10.4f} {pct:>+9.2f}% {dstd:>8.4f} {t:>6.1f} "
                  f"{b:>+9.4f}  {verdict}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
