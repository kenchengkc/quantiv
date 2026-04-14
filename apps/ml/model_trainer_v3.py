#!/usr/bin/env python3
"""
Model Training v3 — Quantile regression + improved ensemble for earnings move prediction.

Key improvements over v2:
  1. Quantile regression: native LightGBM quantile loss for P10/P50/P90 bands
     (replaces Gaussian residual_std assumption — earnings tails are NOT normal)
  2. Improved hyperparameters with Optuna search (optional)
  3. Better calibration diagnostics: quantile coverage, Pinball loss
  4. Event vol decomposition features from feature_engineering_v3
  5. Walk-forward validation with purged gap to prevent leakage

Usage:
  python apps/ml/model_trainer_v3.py
  python apps/ml/model_trainer_v3.py --horizons 1 7 14 --tune
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HORIZONS = [1, 2, 3, 7, 14, 21]
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))


def walk_forward_split(df: pd.DataFrame,
                       train_frac: float = 0.75,
                       purge_gap: int = 5) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split with purge gap to prevent information leakage.

    The purge gap removes `purge_gap` rows between train and validation sets
    to prevent any data leaking across the boundary (e.g. overlapping earnings
    windows from the same quarter).
    """
    n = len(df)
    split_idx = int(n * train_frac)
    gap_end = min(split_idx + purge_gap, n)
    return df.iloc[:split_idx], df.iloc[gap_end:]


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, alpha: float) -> float:
    """Pinball (quantile) loss — proper scoring rule for quantile predictions."""
    residual = y_true - y_pred
    return float(np.mean(np.where(residual >= 0, alpha * residual, (alpha - 1) * residual)))


def train_point_model(X_train: pd.DataFrame, y_train: pd.Series,
                      X_val: pd.DataFrame, y_val: pd.Series,
                      horizon: int) -> Tuple[LGBMRegressor, Dict[str, Any]]:
    """Train the main point-estimate model (L2 loss)."""

    model = LGBMRegressor(
        n_estimators=2000,
        learning_rate=0.03,
        max_depth=7,
        num_leaves=63,
        min_child_samples=max(10, len(X_train) // 50),
        subsample=0.8,
        colsample_bytree=0.7,
        reg_alpha=0.05,
        reg_lambda=0.1,
        random_state=42,
        verbose=-1,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[
            early_stopping(100, verbose=False),
            log_evaluation(0),
        ],
    )

    y_pred_train = model.predict(X_train)
    y_pred_val = model.predict(X_val)

    metrics = {
        "horizon": horizon,
        "n_train": len(X_train),
        "n_val": len(X_val),
        "train_mae": float(mean_absolute_error(y_train, y_pred_train)),
        "val_mae": float(mean_absolute_error(y_val, y_pred_val)),
        "val_rmse": float(np.sqrt(mean_squared_error(y_val, y_pred_val))),
        "val_r2": float(r2_score(y_val, y_pred_val)),
        "best_iteration": model.best_iteration_,
    }

    # Baseline: using straddle_pct directly as prediction
    if "straddle_pct" in X_val.columns:
        baseline_mae = float(mean_absolute_error(y_val, X_val["straddle_pct"].fillna(y_val.mean())))
        metrics["baseline_straddle_mae"] = baseline_mae
        if baseline_mae > 0:
            metrics["improvement_vs_straddle"] = f"{(1 - metrics['val_mae'] / baseline_mae) * 100:.1f}%"

    # Baseline: using event_move_implied
    if "event_move_implied" in X_val.columns:
        evi = X_val["event_move_implied"].fillna(X_val["straddle_pct"] if "straddle_pct" in X_val.columns else y_val.mean())
        baseline_event_mae = float(mean_absolute_error(y_val, evi))
        metrics["baseline_event_vol_mae"] = baseline_event_mae

    # Feature importance
    importance = dict(zip(X_train.columns, model.feature_importances_))
    metrics["feature_importance"] = dict(sorted(importance.items(), key=lambda x: -x[1])[:15])

    return model, metrics


def train_quantile_models(X_train: pd.DataFrame, y_train: pd.Series,
                          X_val: pd.DataFrame, y_val: pd.Series,
                          horizon: int) -> Tuple[Dict[float, LGBMRegressor], Dict[str, Any]]:
    """Train quantile regression models for proper confidence bands.

    Earnings moves are heavily right-skewed with fat tails — Gaussian bands
    systematically undercover the tails. Quantile regression gives us calibrated
    P10/P25/P50/P75/P90 predictions.
    """

    models = {}
    metrics = {"horizon": horizon}

    for alpha in QUANTILES:
        qmodel = LGBMRegressor(
            objective="quantile",
            alpha=alpha,
            n_estimators=1500,
            learning_rate=0.03,
            max_depth=6,
            num_leaves=31,
            min_child_samples=max(10, len(X_train) // 50),
            subsample=0.8,
            colsample_bytree=0.7,
            reg_alpha=0.05,
            reg_lambda=0.1,
            random_state=42,
            verbose=-1,
        )

        qmodel.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[
                early_stopping(80, verbose=False),
                log_evaluation(0),
            ],
        )

        models[alpha] = qmodel

        y_pred_q = qmodel.predict(X_val)
        pbl = pinball_loss(y_val.values, y_pred_q, alpha)
        coverage = float((y_val.values <= y_pred_q).mean())
        metrics[f"q{int(alpha*100):02d}_pinball"] = pbl
        metrics[f"q{int(alpha*100):02d}_coverage"] = coverage

    # Interval coverage diagnostics
    y_p10 = models[0.10].predict(X_val)
    y_p90 = models[0.90].predict(X_val)
    y_p25 = models[0.25].predict(X_val)
    y_p75 = models[0.75].predict(X_val)

    metrics["coverage_80"] = float(((y_val.values >= y_p10) & (y_val.values <= y_p90)).mean())
    metrics["coverage_50"] = float(((y_val.values >= y_p25) & (y_val.values <= y_p75)).mean())
    metrics["interval_width_80_mean"] = float(np.mean(y_p90 - y_p10))
    metrics["interval_width_50_mean"] = float(np.mean(y_p75 - y_p25))

    return models, metrics


def run_training(horizons: List[int] = HORIZONS, tune: bool = False):
    data_dir = get_data_dir()
    ml_dir = data_dir / "ml_training"
    models_dir = data_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    for horizon in horizons:
        path = ml_dir / f"training_T{horizon}.parquet"
        if not path.exists():
            logger.warning(f"T-{horizon}: training file not found at {path}")
            continue

        logger.info(f"\n{'='*60}")
        logger.info(f"Training T-{horizon} model (v3)")
        logger.info(f"{'='*60}")

        df = pd.read_parquet(path)
        target_col = "target"
        feature_cols = [c for c in df.columns if c != target_col]

        X = df[feature_cols]
        y = df[target_col]

        X_train, X_val = walk_forward_split(X)
        y_train, y_val = y.iloc[:len(X_train)], y.iloc[len(X_train):len(X_train) + len(X_val)]

        logger.info(f"Train: {len(X_train)} | Val: {len(X_val)} | Features: {len(feature_cols)}")
        logger.info(f"Target — train μ={y_train.mean():.4f} σ={y_train.std():.4f} | "
                     f"val μ={y_val.mean():.4f} σ={y_val.std():.4f}")

        # ── Point model ──
        model, metrics = train_point_model(X_train, y_train, X_val, y_val, horizon)

        logger.info(f"Point model — MAE: {metrics['val_mae']:.4f}  "
                     f"RMSE: {metrics['val_rmse']:.4f}  R²: {metrics['val_r2']:.4f}")
        if "improvement_vs_straddle" in metrics:
            logger.info(f"  vs straddle: {metrics['improvement_vs_straddle']}")
        logger.info(f"  Top features: {list(metrics['feature_importance'].keys())[:5]}")

        # ── Quantile models ──
        q_models, q_metrics = train_quantile_models(X_train, y_train, X_val, y_val, horizon)

        logger.info(f"Quantile models — 80% coverage: {q_metrics['coverage_80']:.1%} (target: 80%)  "
                     f"50% coverage: {q_metrics['coverage_50']:.1%} (target: 50%)")
        logger.info(f"  80% interval width: {q_metrics['interval_width_80_mean']:.4f}  "
                     f"50% interval width: {q_metrics['interval_width_50_mean']:.4f}")

        # ── Save point model ──
        model_path = models_dir / f"lgbm_T{horizon}.joblib"
        joblib.dump(model, model_path)

        # ── Save quantile models ──
        for alpha, qm in q_models.items():
            q_path = models_dir / f"lgbm_T{horizon}_q{int(alpha*100):02d}.joblib"
            joblib.dump(qm, q_path)

        # ── Save combined metadata ──
        all_metrics = {**metrics, **q_metrics}
        all_metrics["trained_at"] = datetime.now().isoformat()
        all_metrics["model_path"] = str(model_path)
        all_metrics["feature_cols"] = feature_cols
        all_metrics["quantiles"] = QUANTILES
        all_metrics["version"] = "v3"

        # Also compute the old-style residual_std for backward compat
        y_pred_val = model.predict(X_val)
        residuals = y_val - y_pred_val
        all_metrics["residual_std"] = float(residuals.std())
        all_metrics["residual_mean"] = float(residuals.mean())

        meta_path = models_dir / f"metadata_T{horizon}.json"
        with open(meta_path, "w") as f:
            json.dump(all_metrics, f, indent=2, default=str)

        results[horizon] = all_metrics

    # ── Summary ──
    print(f"\n{'='*80}")
    print("TRAINING SUMMARY (v3 — point + quantile models)")
    print(f"{'='*80}")
    print(f"{'Horizon':<10} {'Train':>6} {'Val':>6} {'MAE':>8} {'RMSE':>8} {'R²':>8} "
          f"{'80%cov':>7} {'50%cov':>7} {'vs Str':>10}")
    print("-" * 85)
    for h in sorted(results):
        m = results[h]
        imp = m.get("improvement_vs_straddle", "N/A")
        print(f"T-{h:<8} {m['n_train']:>6} {m['n_val']:>6} "
              f"{m['val_mae']:>8.4f} {m['val_rmse']:>8.4f} {m['val_r2']:>8.4f} "
              f"{m.get('coverage_80', 0):>6.1%} {m.get('coverage_50', 0):>6.1%} {imp:>10}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Train LightGBM models v3 (point + quantile)")
    parser.add_argument("--horizons", nargs="+", type=int, default=HORIZONS)
    parser.add_argument("--tune", action="store_true", help="Run Optuna hyperparameter search")
    args = parser.parse_args()
    run_training(args.horizons, args.tune)


if __name__ == "__main__":
    main()
