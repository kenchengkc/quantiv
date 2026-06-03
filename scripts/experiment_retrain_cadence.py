#!/usr/bin/env python3
"""Does retraining more frequently improve out-of-sample accuracy?

Controlled walk-forward: over a fixed recent test window, predict every OOS
earnings event exactly once, varying ONLY how fresh the training cutoff is
(i.e. how often we "retrain"). Hyperparameters are fixed (no Optuna) so the
comparison isolates retrain *frequency* from tuning noise, and production-style
time-decay weighting (half-life 0.5y) is applied so "recent matters" is already
baked in — exactly like the weekly job.

For cadence D days: partition the test window into D-day bins by earnings_date;
for each bin [t, t+D) train on all events with earnings_date < t (expanding,
decay-weighted) and predict that bin. Smaller D = fresher model per event =
"retrain every D days". A single bin spanning the whole window = "train once,
never retrain" (the staleness extreme).

Usage:
  python scripts/experiment_retrain_cadence.py --horizons 1 2 3 7 --test-days 150
"""

from __future__ import annotations

import argparse
import sys
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

PARAMS = dict(DEFAULT_PARAMS)
PARAMS.update(n_estimators=400, random_state=42, verbose=-1, n_jobs=-1)


def decay_weights(train_dates: pd.Series, cutoff: pd.Timestamp, half_life_years: float) -> np.ndarray:
    days_old = (cutoff - train_dates).dt.days.clip(lower=0).to_numpy(dtype=float)
    return np.exp(-days_old / (365.0 * half_life_years))


def run_cadence(X, y, ed, test_start, test_end, cadence_days, half_life, min_train):
    preds, actuals, straddle = [], [], []
    n_trainings = 0
    has_straddle = "straddle_pct" in X.columns
    t = test_start
    while t <= test_end:
        bin_end = t + pd.Timedelta(days=cadence_days)
        tr = (ed < t).to_numpy()
        te = ((ed >= t) & (ed < bin_end) & (ed <= test_end)).to_numpy()
        if te.sum() == 0 or tr.sum() < min_train:
            t = bin_end
            continue
        w = decay_weights(ed[tr], t, half_life)
        model = LGBMRegressor(**PARAMS)
        model.fit(X[tr], y[tr], sample_weight=w)
        preds.append(model.predict(X[te]))
        actuals.append(y[te].to_numpy())
        if has_straddle:
            straddle.append(X.loc[te, "straddle_pct"].to_numpy())
        n_trainings += 1
        t = bin_end

    actuals = np.concatenate(actuals)
    preds = np.concatenate(preds)
    mae = mean_absolute_error(actuals, preds)
    base = None
    if straddle:
        s = np.concatenate(straddle)
        ok = ~np.isnan(s)
        if ok.any():
            base = mean_absolute_error(actuals[ok], s[ok])
    return {"cadence_days": cadence_days, "n_events": len(actuals),
            "n_trainings": n_trainings, "mae": mae, "straddle_mae": base}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 2, 3, 7])
    ap.add_argument("--cadences", type=int, nargs="+", default=[2, 7, 14, 30, 90])
    ap.add_argument("--test-days", type=int, default=150, help="OOS window length (days, by earnings_date)")
    ap.add_argument("--half-life", type=float, default=0.5)
    ap.add_argument("--min-train", type=int, default=1000)
    args = ap.parse_args()

    ml_dir = get_data_dir() / "ml_training"
    for h in args.horizons:
        df = load_training_frame(ml_dir, h)
        X, y, ed = feature_target_split(df)
        ed = pd.to_datetime(ed)
        data_end = ed.max()
        test_start = (data_end - pd.Timedelta(days=args.test_days)).normalize()
        test_end = data_end

        print(f"\n=== T{h}  | data → {data_end.date()}  | OOS {test_start.date()}..{test_end.date()} "
              f"({((ed >= test_start) & (ed <= test_end)).sum()} rows) ===", flush=True)

        rows = []
        # "train once" = one bin over the whole window (max staleness baseline)
        once = run_cadence(X, y, ed, test_start, test_end, args.test_days + 1, args.half_life, args.min_train)
        once["cadence_days"] = f"once({args.test_days}d)"
        for cd in args.cadences:
            rows.append(run_cadence(X, y, ed, test_start, test_end, cd, args.half_life, args.min_train))
        rows.append(once)

        weekly = next((r for r in rows if r["cadence_days"] == 7), None)
        wk_mae = weekly["mae"] if weekly else None
        print(f"{'retrain':>14} {'#trains':>8} {'#events':>8} {'OOS MAE':>9} {'vs wk7':>8} {'vs straddle':>12}")
        print("-" * 64)
        for r in rows:
            vs_wk = f"{(r['mae']/wk_mae - 1)*100:+.2f}%" if wk_mae else "—"
            vs_str = f"{(r['mae']/r['straddle_mae'] - 1)*100:+.1f}%" if r["straddle_mae"] else "—"
            label = r["cadence_days"] if isinstance(r["cadence_days"], str) else f"{r['cadence_days']}d"
            print(f"{label:>14} {r['n_trainings']:>8} {r['n_events']:>8} "
                  f"{r['mae']:>9.4f} {vs_wk:>8} {vs_str:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
