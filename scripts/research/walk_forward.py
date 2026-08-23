#!/usr/bin/env python3
"""
Walk-forward validation (diagnostic only — does not write production models).

Trains a point-estimate LightGBM on a rolling calendar train window, evaluates
MAE on the next quarter, steps forward one quarter at a time, and plots val
MAE vs validation quarter.

Example (matches a 3Q-train / 1Q-val narrative for 2024):
  - Val 2024Q4 with train = 2024Q1–Q3
  - Val 2025Q1 with train = 2024Q2–Q4
  - Val 2025Q2 with train = 2024Q3–2025Q1  (not "Q2'24–Q1'25" — use --train-quarters 4 for that)

Usage (from repo root, ML venv with lightgbm + matplotlib):
  python scripts/research/walk_forward.py
  python scripts/research/walk_forward.py --horizon 7 --train-quarters 4 --first-val 2025-2
  python scripts/research/walk_forward.py --output /tmp/wf_mae.png

Requires training parquet from feature_engineering.py (ml_training/training_T*.parquet).
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation
from sklearn.metrics import mean_absolute_error

# Keep in sync with apps/ml/model_trainer.py DEFAULT_PARAMS (avoid importing
# model_trainer, which pulls in Optuna).
DEFAULT_PARAMS: Dict[str, Any] = {
    "n_estimators": 2000,
    "learning_rate": 0.03,
    "max_depth": 7,
    "num_leaves": 63,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "reg_alpha": 0.05,
    "reg_lambda": 0.1,
}


_REPO = Path(__file__).resolve().parents[2]  # scripts/research/ → repo root


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(_REPO / "data")))


YearQuarter = Tuple[int, int]  # (year, quarter 1..4)


def quarter_start(y: int, q: int) -> pd.Timestamp:
    month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
    return pd.Timestamp(year=y, month=month, day=1)


def quarter_end(y: int, q: int) -> pd.Timestamp:
    last = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[q]
    return pd.Timestamp(year=y, month=last[0], day=last[1])


def quarters_before_val(val_y: int, val_q: int, n: int) -> List[YearQuarter]:
    """The n calendar quarters immediately preceding (val_y, val_q), oldest first."""
    out_rev: List[YearQuarter] = []
    y, q = val_y, val_q
    for _ in range(n):
        q -= 1
        if q == 0:
            y -= 1
            q = 4
        out_rev.append((y, q))
    return list(reversed(out_rev))


def mask_in_quarters(dates: pd.Series, quarters: Sequence[YearQuarter]) -> pd.Series:
    m = pd.Series(False, index=dates.index)
    for y, q in quarters:
        m |= (dates >= quarter_start(y, q)) & (dates <= quarter_end(y, q))
    return m


def parse_yq(s: str) -> YearQuarter:
    t = s.strip().upper().replace("Q", "-")
    parts = [p for p in t.split("-") if p]
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    raise ValueError(f"Expected YYYY-QN e.g. 2024-4 or 2024Q4, got {s!r}")


def yq_label(y: int, q: int) -> str:
    return f"{y}Q{q}"


def iter_val_quarters(first: YearQuarter, last: YearQuarter) -> Iterable[YearQuarter]:
    y, q = first
    ly, lq = last
    while (y, q) <= (ly, lq):
        yield (y, q)
        q += 1
        if q == 5:
            q = 1
            y += 1


@dataclass
class FoldResult:
    val_label: str
    train_quarters: str
    n_train: int
    n_val: int
    val_mae: float
    baseline_straddle_mae: float | None


def load_training_frame(ml_dir: Path, horizon: int) -> pd.DataFrame:
    path = ml_dir / f"training_T{horizon}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path} — run apps/ml/feature_engineering.py first.")
    df = pd.read_parquet(path)
    if "__earnings_date" not in df.columns:
        raise ValueError("Parquet must include __earnings_date for calendar splits.")
    df = df.copy()
    df["__earnings_date"] = pd.to_datetime(df["__earnings_date"])
    df = df.sort_values("__earnings_date").reset_index(drop=True)
    return df


def feature_target_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    target_col = "target"
    meta_cols = [c for c in df.columns if c.startswith("__")]
    feature_cols = [c for c in df.columns if c != target_col and c not in meta_cols]
    X = df[feature_cols]
    y = df[target_col]
    ed = df["__earnings_date"]
    return X, y, ed


def train_one_fold_quick(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    quick_trees: int,
) -> float:
    """Fit a single diagnostic model; return val MAE (same metric as model_trainer)."""
    p = dict(DEFAULT_PARAMS)
    p["n_estimators"] = quick_trees
    p.setdefault("min_child_samples", max(10, len(X_train) // 50))
    p["random_state"] = 42
    p["verbose"] = -1
    model = LGBMRegressor(**p)
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[early_stopping(80, verbose=False), log_evaluation(0)],
    )
    pred = model.predict(X_val)
    return float(mean_absolute_error(y_val, pred))


def run_walk_forward(
    df: pd.DataFrame,
    train_quarters: int,
    first_val: YearQuarter,
    last_val: YearQuarter,
    min_train_rows: int,
    min_val_rows: int,
    quick_trees: int,
) -> List[FoldResult]:
    X_all, y_all, ed_all = feature_target_split(df)
    results: List[FoldResult] = []

    for vy, vq in iter_val_quarters(first_val, last_val):
        train_qs = quarters_before_val(vy, vq, train_quarters)
        tr_m = mask_in_quarters(ed_all, train_qs)
        va_m = (ed_all >= quarter_start(vy, vq)) & (ed_all <= quarter_end(vy, vq))

        n_tr, n_va = int(tr_m.sum()), int(va_m.sum())
        if n_tr < min_train_rows or n_va < min_val_rows:
            continue

        X_train = X_all.loc[tr_m].reset_index(drop=True)
        y_train = y_all.loc[tr_m].reset_index(drop=True)
        X_val = X_all.loc[va_m].reset_index(drop=True)
        y_val = y_all.loc[va_m].reset_index(drop=True)

        val_mae = train_one_fold_quick(
            X_train, y_train, X_val, y_val, quick_trees=quick_trees,
        )

        baseline = None
        if "straddle_pct" in X_val.columns:
            baseline = float(
                mean_absolute_error(
                    y_val,
                    X_val["straddle_pct"].fillna(y_train.mean()),
                ),
            )

        results.append(
            FoldResult(
                val_label=yq_label(vy, vq),
                train_quarters=" → ".join(yq_label(y, q) for y, q in train_qs),
                n_train=n_tr,
                n_val=n_va,
                val_mae=val_mae,
                baseline_straddle_mae=baseline,
            ),
        )

    return results


def plot_mae(results: List[FoldResult], out_path: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    if not results:
        raise SystemExit("No folds produced — widen date range or lower min_* rows.")

    labels = [r.val_label for r in results]
    maes = [r.val_mae for r in results]
    fig, ax = plt.subplots(figsize=(10, 4.5), layout="constrained")
    ax.plot(labels, maes, marker="o", linewidth=1.5, label="Val MAE (model)")
    bases = [r.baseline_straddle_mae for r in results if r.baseline_straddle_mae is not None]
    if len(bases) == len(results):
        ax.plot(labels, bases, marker="x", linestyle="--", alpha=0.7, label="Val MAE (straddle %)")

    ax.set_xlabel("Validation quarter")
    ax.set_ylabel("MAE (realized move %)")
    ax.set_title(title)
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=35, ha="right")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward val MAE by quarter (diagnostic).")
    parser.add_argument("--horizon", type=int, default=7, help="Horizon T (default 7).")
    parser.add_argument(
        "--train-quarters",
        type=int,
        default=3,
        help="Number of consecutive calendar quarters before each val quarter (default 3).",
    )
    parser.add_argument(
        "--first-val",
        type=str,
        default="2024-4",
        help="First validation quarter YYYY-Q (default 2024-4 i.e. Q4 2024).",
    )
    parser.add_argument(
        "--last-val",
        type=str,
        default=None,
        help="Last validation quarter YYYY-Q (default: latest quarter with data).",
    )
    parser.add_argument("--min-train-rows", type=int, default=80)
    parser.add_argument("--min-val-rows", type=int, default=15)
    parser.add_argument("--quick-trees", type=int, default=500, help="n_estimators cap per fold.")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="PNG path (default: DATA_DIR/ml_training/walk_forward_mae_T{horizon}.png).",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Optional CSV path for fold table.",
    )
    args = parser.parse_args()

    data_dir = get_data_dir()
    ml_dir = data_dir / "ml_training"
    df = load_training_frame(ml_dir, args.horizon)
    ed = df["__earnings_date"]
    data_last = ed.max()
    data_first = ed.min()

    first_val = parse_yq(args.first_val)
    if args.last_val:
        last_val = parse_yq(args.last_val)
    else:
        ly, lq = int(data_last.year), (int(data_last.month) - 1) // 3 + 1
        last_val = (ly, lq)

    print(f"Data: {data_first.date()} → {data_last.date()}  (n={len(df)})")
    print(f"Horizon T-{args.horizon}  train_quarters={args.train_quarters}  val {yq_label(*first_val)} … {yq_label(*last_val)}")

    results = run_walk_forward(
        df,
        train_quarters=args.train_quarters,
        first_val=first_val,
        last_val=last_val,
        min_train_rows=args.min_train_rows,
        min_val_rows=args.min_val_rows,
        quick_trees=args.quick_trees,
    )

    print(f"\n{'Val':<10} {'n_tr':>6} {'n_va':>6} {'MAE':>8} {'straddle MAE':>14}")
    print("-" * 52)
    for r in results:
        bs = f"{r.baseline_straddle_mae:.4f}" if r.baseline_straddle_mae is not None else "—"
        print(f"{r.val_label:<10} {r.n_train:>6} {r.n_val:>6} {r.val_mae:>8.4f} {bs:>14}")

    out_png = Path(args.output) if args.output else ml_dir / f"walk_forward_mae_T{args.horizon}.png"
    title = (
        f"Walk-forward val MAE  |  T-{args.horizon}  |  train={args.train_quarters}Q / val=1Q"
    )
    try:
        plot_mae(results, out_png, title=title)
        print(f"\nWrote plot: {out_png}")
    except ImportError:
        print("\nmatplotlib not installed — skipped plot. pip install matplotlib or use apps/ml venv.")
        out_png = None

    if args.csv:
        csv_path = Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([r.__dict__ for r in results]).to_csv(csv_path, index=False)
        print(f"Wrote CSV: {csv_path}")


if __name__ == "__main__":
    main()
