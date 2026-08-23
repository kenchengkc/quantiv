#!/usr/bin/env python3
"""Rigorous, paired search for model improvements (not noise).

The target is realized_move_pct. Raw straddle/implied move is never treated as
truth; it is the naive benchmark the model must beat against realized outcomes.
In this data, straddles structurally over-predict realized moves and the
realized-vs-implied cross-sectional correlation is low, so most transforms of
existing implied features should be expected to test null unless they add real
information.

For each monthly walk-forward fold (expanding train, production half-life
decay weights by default) and each random seed, every variant is trained on the
SAME (X_train, y_train, w) and evaluated on the SAME X_test. So the
variant-vs-baseline delta per (fold,seed) is paired — it removes seed/fold
variance and isolates the change. We aggregate the paired ΔMAE across
folds×seeds and report mean, std, and a t-stat; an improvement only counts if
ΔMAE < 0 and |t| >= 2.

Round is chosen with --round: objectives | validate | ensemble | skew | dist.

Usage:
  python scripts/research/experiment_model_improvements.py --round objectives
  python scripts/research/experiment_model_improvements.py --round validate --oos-offset 150
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
SIGNIFICANT_T = 2.0

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
    if round_name == "ensemble":
        l1 = {"objective": "regression_l1"}
        return [
            ("L1_single", lgbm(l1), ID, None),
            ("L1_bag3", (lambda seed: SeedBag(l1, 3, seed)), ID, None),
            ("L1_bag5", (lambda seed: SeedBag(l1, 5, seed)), ID, None),
        ]
    if round_name == "skew":
        # Baseline = L1 WITHOUT the new options-shape features; variants add
        # skew (directional), smile width (magnitude), or both. ΔMAE isolates
        # each one's contribution on top of the committed L1 model.
        l1 = {"objective": "regression_l1"}
        ALL = ["skew_25d", "skew_pc_ratio", "smile_width"]
        return [
            ("L1_none", lgbm(l1), ID, ALL),
            ("L1_skew", lgbm(l1), ID, ["smile_width"]),
            ("L1_smile", lgbm(l1), ID, ["skew_25d", "skew_pc_ratio"]),
            ("L1_all", lgbm(l1), ID, None),
        ]
    if round_name == "dist":
        # Baseline = committed L1 WITHOUT the new distribution-shape features;
        # variant adds the fat-tail/skew summary of past moves. ΔMAE isolates
        # whether tail shape buys point accuracy on top of mean/median history.
        l1 = {"objective": "regression_l1"}
        DIST = ["hist_move_p75_8q", "hist_move_p90_8q", "hist_move_max_12q",
                "hist_move_kurt_8q", "hist_move_last"]
        return [
            ("L1_base", lgbm(l1), ID, DIST),
            ("L1_dist", lgbm(l1), ID, None),
        ]
    raise ValueError(round_name)


def make_folds(ed: pd.Series, n_folds: int, test_days: int, offset: int = 0):
    end = (ed.max() - pd.Timedelta(days=offset)).normalize()
    out = []
    for i in range(n_folds):
        c0 = (end - pd.Timedelta(days=test_days * (n_folds - i))).normalize()
        out.append((c0, c0 + pd.Timedelta(days=test_days)))
    return out


def summarize_implied_benchmark(y_parts: list[np.ndarray], implied_parts: list[np.ndarray]) -> dict | None:
    if not y_parts or not implied_parts:
        return None
    y = np.concatenate(y_parts).astype(float)
    implied = np.concatenate(implied_parts).astype(float)
    mask = np.isfinite(y) & np.isfinite(implied) & (implied > 0)
    if mask.sum() < 2:
        return None
    y = y[mask]
    implied = implied[mask]
    denom = float(np.dot(implied, implied))
    slope = float(np.dot(y, implied) / denom) if denom > 0 else np.nan
    corr = float(np.corrcoef(y, implied)[0, 1]) if np.std(y) > 0 and np.std(implied) > 0 else np.nan
    return {
        "n": int(mask.sum()),
        "mae": float(mean_absolute_error(y, implied)),
        "bias": float(np.mean(implied - y)),
        "slope": slope,
        "median_ratio": float(np.median(y / implied)),
        "corr": corr,
    }


class SeedBag:
    """Average several LGBM models trained with different seeds (variance
    reduction). Built per (fold) — each seed gets a distinct bag seed offset."""

    def __init__(self, extra, n_bag, base_seed):
        self.extra = extra
        self.n_bag = n_bag
        self.base_seed = base_seed
        self.models = []

    def fit(self, X, y, sample_weight=None):
        self.models = []
        for k in range(self.n_bag):
            m = LGBMRegressor(**{**BASE, "random_state": self.base_seed * 100 + k, **self.extra})
            m.fit(X, y, sample_weight=sample_weight)
            self.models.append(m)
        return self

    def predict(self, X):
        return np.mean([m.predict(X) for m in self.models], axis=0)


def run_horizon(horizon, specs, seeds, n_folds, test_days, hl, min_train, offset=0, ml_dir=None):
    df = load_training_frame(ml_dir or (get_data_dir() / "ml_training"), horizon)
    X, y, ed = feature_target_split(df)
    ed = pd.to_datetime(ed)
    folds = make_folds(ed, n_folds, test_days, offset)

    names = [s[0] for s in specs]
    maes = {n: [] for n in names}
    paired = {n: [] for n in names}  # ΔMAE vs baseline per (fold,seed)
    bias = {n: [] for n in names}    # mean(pred - actual): >0 over-predicts
    implied_y_parts: list[np.ndarray] = []
    implied_parts: list[np.ndarray] = []
    n_obs = 0
    for (c0, c1) in folds:
        tr = (ed < c0).to_numpy()
        te = ((ed >= c0) & (ed < c1)).to_numpy()
        if te.sum() < 30 or tr.sum() < min_train:
            continue
        Xtr, ytr = X[tr], y[tr].to_numpy()
        Xte, yte = X[te], y[te].to_numpy()
        if "straddle_pct" in Xte.columns:
            implied = Xte["straddle_pct"].to_numpy(dtype=float)
            mask = np.isfinite(yte) & np.isfinite(implied) & (implied > 0)
            if mask.any():
                implied_y_parts.append(yte[mask])
                implied_parts.append(implied[mask])
        w = decay_w(ed[tr], c0, hl)
        for seed in seeds:
            fold_mae = {}
            for name, fac, (fwd, inv), drop in specs:
                Xtr_v = Xtr.drop(columns=drop, errors="ignore") if drop else Xtr
                Xte_v = Xte.drop(columns=drop, errors="ignore") if drop else Xte
                m = fac(seed)
                m.fit(Xtr_v, fwd(ytr), sample_weight=w)
                pred = inv(m.predict(Xte_v))
                fold_mae[name] = mean_absolute_error(yte, pred)
                bias[name].append(float(np.mean(pred - yte)))
            base = fold_mae[names[0]]
            for name in names:
                maes[name].append(fold_mae[name])
                paired[name].append(fold_mae[name] - base)
            n_obs += 1
    return maes, paired, bias, n_obs, summarize_implied_benchmark(implied_y_parts, implied_parts)


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
    ap.add_argument("--ml-dir", default=None, help="Override training parquet dir")
    ap.add_argument("--n-estimators", type=int, default=None,
                    help="Override LightGBM tree count for quick experiment screens")
    args = ap.parse_args()
    if args.n_estimators is not None:
        BASE["n_estimators"] = args.n_estimators
    ml_dir = Path(args.ml_dir) if args.ml_dir else None

    specs = specs_for(args.round)
    print(f"ROUND={args.round}  horizons={args.horizons}  seeds={args.seeds}  "
          f"folds={args.folds}x{args.test_days}d\n")

    for h in args.horizons:
        maes, paired, bias, n, implied = run_horizon(
            h, specs, args.seeds, args.folds, args.test_days, args.half_life,
            args.min_train, args.oos_offset, ml_dir,
        )
        print(f"=== T{h}  ({n} fold×seed obs, oos_offset={args.oos_offset}d) ===")
        if implied:
            print(
                "raw implied benchmark vs realized: "
                f"MAE={implied['mae']:.4f}  "
                f"bias(straddle-realized)={implied['bias']:+.4f}  "
                f"realized≈{implied['slope']:.2f}×straddle  "
                f"median(y/straddle)={implied['median_ratio']:.2f}  "
                f"corr={implied['corr']:.2f}  n={implied['n']}"
            )
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
            elif dmean < 0 and abs(t) >= SIGNIFICANT_T:
                verdict = "✅ better (sig)"
            elif dmean < 0:
                verdict = "~ better (noisy)"
            elif abs(t) >= SIGNIFICANT_T:
                verdict = "❌ worse (sig)"
            else:
                verdict = "~ worse (noisy)"
            print(f"{name:>16} {mae:>10.4f} {pct:>+9.2f}% {dstd:>8.4f} {t:>6.1f} "
                  f"{b:>+9.4f}  {verdict}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
