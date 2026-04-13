#!/usr/bin/env python3
"""
LightGBM Model Training v2 — walk-forward validation with Parkinson/IV-RV features.

For each horizon T-k:
  1. Load training data from feature_engineering_v2
  2. Walk-forward split: train on older data, validate on newer data
  3. Train LightGBM with early stopping
  4. Evaluate: MAE, coverage of confidence bands, calibration
  5. Save model + metadata

Usage:
  python apps/ml/model_trainer_v2.py
  python apps/ml/model_trainer_v2.py --horizons 1 7 14
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
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HORIZONS = [1, 2, 3, 7, 14, 21]


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))


# ---------------------------------------------------------------------------
# Walk-forward split
# ---------------------------------------------------------------------------
def walk_forward_split(df: pd.DataFrame, train_frac: float = 0.75) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split — first 75% train, last 25% test."""
    n = len(df)
    split_idx = int(n * train_frac)
    return df.iloc[:split_idx], df.iloc[split_idx:]


# ---------------------------------------------------------------------------
# Train one model
# ---------------------------------------------------------------------------
def train_model(X_train: pd.DataFrame, y_train: pd.Series,
                X_val: pd.DataFrame, y_val: pd.Series,
                horizon: int) -> Tuple[LGBMRegressor, Dict[str, Any]]:
    """Train a LightGBM model with early stopping."""

    model = LGBMRegressor(
        n_estimators=1000,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        min_child_samples=max(10, len(X_train) // 50),
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=42,
        verbose=-1,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[
            # early stopping after 50 rounds of no improvement
            __import__("lightgbm").early_stopping(50, verbose=False),
            __import__("lightgbm").log_evaluation(0),
        ],
    )

    # Predictions
    y_pred_train = model.predict(X_train)
    y_pred_val = model.predict(X_val)

    # Metrics
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

    # Residual std for confidence bands
    residuals = y_val - y_pred_val
    metrics["residual_std"] = float(residuals.std())
    metrics["residual_mean"] = float(residuals.mean())

    # Coverage: what fraction of actuals fall within predicted ± 1σ / 2σ
    within_1s = np.abs(residuals) <= metrics["residual_std"]
    within_2s = np.abs(residuals) <= 2 * metrics["residual_std"]
    metrics["coverage_68"] = float(within_1s.mean())
    metrics["coverage_95"] = float(within_2s.mean())

    # Feature importance
    importance = dict(zip(X_train.columns, model.feature_importances_))
    metrics["feature_importance"] = dict(sorted(importance.items(), key=lambda x: -x[1])[:10])

    # Baseline comparison (just using straddle_pct as the prediction)
    if "straddle_pct" in X_val.columns:
        baseline_mae = float(mean_absolute_error(y_val, X_val["straddle_pct"]))
        metrics["baseline_straddle_mae"] = baseline_mae
        metrics["improvement_vs_baseline"] = f"{(1 - metrics['val_mae']/baseline_mae)*100:.1f}%"

    return model, metrics


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_training(horizons: List[int] = HORIZONS):
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

        logger.info(f"\n{'='*50}")
        logger.info(f"Training T-{horizon} model")
        logger.info(f"{'='*50}")

        df = pd.read_parquet(path)
        target_col = "target"
        feature_cols = [c for c in df.columns if c != target_col]

        X = df[feature_cols]
        y = df[target_col]

        # Walk-forward split
        X_train, X_val = walk_forward_split(X)
        y_train, y_val = y.iloc[:len(X_train)], y.iloc[len(X_train):]

        logger.info(f"Train: {len(X_train)} | Val: {len(X_val)}")
        logger.info(f"Target — train μ={y_train.mean():.4f} | val μ={y_val.mean():.4f}")

        # Train
        model, metrics = train_model(X_train, y_train, X_val, y_val, horizon)

        # Log results
        logger.info(f"Val MAE:  {metrics['val_mae']:.4f}")
        logger.info(f"Val RMSE: {metrics['val_rmse']:.4f}")
        logger.info(f"Val R²:   {metrics['val_r2']:.4f}")
        logger.info(f"Coverage 68%: {metrics['coverage_68']:.2%}")
        logger.info(f"Coverage 95%: {metrics['coverage_95']:.2%}")
        if "improvement_vs_baseline" in metrics:
            logger.info(f"vs Straddle baseline: {metrics['improvement_vs_baseline']}")
        logger.info(f"Top features: {list(metrics['feature_importance'].keys())[:5]}")

        # Save model
        model_path = models_dir / f"lgbm_T{horizon}.joblib"
        joblib.dump(model, model_path)
        logger.info(f"Saved model → {model_path}")

        # Save metadata
        meta = {
            **metrics,
            "trained_at": datetime.now().isoformat(),
            "model_path": str(model_path),
            "feature_cols": feature_cols,
        }
        meta_path = models_dir / f"metadata_T{horizon}.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2, default=str)

        results[horizon] = metrics

    # Summary
    print(f"\n{'='*60}")
    print("TRAINING SUMMARY")
    print(f"{'='*60}")
    print(f"{'Horizon':<10} {'Train':>6} {'Val':>6} {'MAE':>8} {'RMSE':>8} {'R²':>8} {'68%':>6} {'95%':>6} {'vs Base':>10}")
    print("-" * 75)
    for h in sorted(results):
        m = results[h]
        imp = m.get("improvement_vs_baseline", "N/A")
        print(f"T-{h:<8} {m['n_train']:>6} {m['n_val']:>6} "
              f"{m['val_mae']:>8.4f} {m['val_rmse']:>8.4f} {m['val_r2']:>8.4f} "
              f"{m['coverage_68']:>5.1%} {m['coverage_95']:>5.1%} {imp:>10}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Train LightGBM models (v2)")
    parser.add_argument("--horizons", nargs="+", type=int, default=HORIZONS)
    args = parser.parse_args()
    run_training(args.horizons)


if __name__ == "__main__":
    main()
