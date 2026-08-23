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
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import optuna
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation
from ml.model_artifact import save_native_model
from ml.quantiles import rearrange_quantile_array
from ml.training_split import chronological_train_val_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

# Quiet Optuna's per-trial INFO chatter; we log a one-liner per study instead.
optuna.logging.set_verbosity(optuna.logging.WARNING)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HORIZONS = [1, 2, 3, 7, 14, 21]
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, alpha: float) -> float:
    """Pinball (quantile) loss — proper scoring rule for quantile predictions."""
    residual = y_true - y_pred
    return float(np.mean(np.where(residual >= 0, alpha * residual, (alpha - 1) * residual)))


# Default LightGBM hyperparameters used when tuning is off. Reasonable
# starting point for ~10–30k row tabular regression.
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


def tune_hyperparameters(X_train: pd.DataFrame, y_train: pd.Series,
                         X_val: pd.DataFrame, y_val: pd.Series,
                         horizon: int, n_trials: int = 30,
                         sample_weight: np.ndarray | None = None) -> Dict[str, Any]:
    """Run Optuna search over LightGBM hyperparameters; return the best set.

    Objective: minimize unweighted val MAE so the score is directly comparable
    across runs (same training set + sample_weight effect, same val split).
    """

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": 2000,  # capped by early_stopping
            # L1/MAE objective so the search tunes the same loss we deploy and
            # score on (see train_point_model docstring).
            "objective": "regression_l1",
            "learning_rate":      trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "num_leaves":         trial.suggest_int("num_leaves", 15, 255),
            "max_depth":          trial.suggest_int("max_depth", 4, 12),
            "min_child_samples":  trial.suggest_int("min_child_samples", 5, 100),
            "subsample":          trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree":   trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_alpha":          trial.suggest_float("reg_alpha", 0.0, 1.0),
            "reg_lambda":         trial.suggest_float("reg_lambda", 0.0, 1.0),
            "random_state": 42,
            "verbose": -1,
        }
        m = LGBMRegressor(**params)
        m.fit(
            X_train, y_train,
            sample_weight=sample_weight,
            eval_set=[(X_val, y_val)],
            callbacks=[early_stopping(50, verbose=False), log_evaluation(0)],
        )
        return float(mean_absolute_error(y_val, m.predict(X_val)))

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    logger.info(f"  T-{horizon} tuning: best val MAE = {study.best_value:.4f} "
                f"after {n_trials} trials")
    return study.best_params


def train_point_model(X_train: pd.DataFrame, y_train: pd.Series,
                      X_val: pd.DataFrame, y_val: pd.Series,
                      horizon: int,
                      sample_weight: np.ndarray | None = None,
                      params: Dict[str, Any] | None = None) -> Tuple[LGBMRegressor, Dict[str, Any]]:
    """Train the main point-estimate model (L1 / MAE loss).

    L1 predicts the conditional MEDIAN, which is the MAE-optimal point forecast
    for this right-skewed target. A paired walk-forward test (5 folds × 4 seeds)
    showed 5–9% lower OOS MAE than the previous L2 (squared-error) objective,
    consistent and significant across horizons (t = −4 to −8.5), and it aligns
    the point estimate with the p50 quantile model.
    """

    p = dict(DEFAULT_PARAMS if params is None else params)
    p.setdefault("objective", "regression_l1")  # MAE loss — see docstring
    p.setdefault("min_child_samples", max(10, len(X_train) // 50))
    p.setdefault("n_estimators", 2000)
    p["random_state"] = 42
    p["verbose"] = -1
    model = LGBMRegressor(**p)

    # `sample_weight` only affects training loss. eval_set stays unweighted so
    # val MAE remains an honest, comparable score across runs.
    model.fit(
        X_train, y_train,
        sample_weight=sample_weight,
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
                          horizon: int,
                          sample_weight: np.ndarray | None = None,
                          params: Dict[str, Any] | None = None) -> Tuple[Dict[float, LGBMRegressor], Dict[str, Any]]:
    """Train quantile regression models for proper confidence bands.

    Earnings moves are heavily right-skewed with fat tails — Gaussian bands
    systematically undercover the tails. Quantile regression gives us calibrated
    P10/P25/P50/P75/P90 predictions.
    """

    models = {}
    metrics = {"horizon": horizon}
    raw_predictions = {}

    # Quantile models share the tuned regularization regime from the point
    # model — same data, same regularization needs. Override n_estimators and
    # cap depth/leaves slightly for quantile stability.
    base = dict(params if params is not None else DEFAULT_PARAMS)
    base["n_estimators"] = 1500
    base.setdefault("min_child_samples", max(10, len(X_train) // 50))
    # Cap quantile-specific complexity to avoid unstable extreme-tail fits.
    base["max_depth"] = min(base.get("max_depth", 6), 6)
    base["num_leaves"] = min(base.get("num_leaves", 31), 31)

    for alpha in QUANTILES:
        qmodel = LGBMRegressor(
            objective="quantile",
            alpha=alpha,
            **base,
            random_state=42,
            verbose=-1,
        )

        qmodel.fit(
            X_train, y_train,
            sample_weight=sample_weight,
            eval_set=[(X_val, y_val)],
            callbacks=[
                early_stopping(80, verbose=False),
                log_evaluation(0),
            ],
        )

        models[alpha] = qmodel

        y_pred_q = qmodel.predict(X_val)
        raw_predictions[alpha] = y_pred_q

    raw_matrix = np.column_stack([raw_predictions[alpha] for alpha in QUANTILES])
    rearranged = rearrange_quantile_array(raw_matrix)
    crossings = np.any(np.diff(raw_matrix, axis=1) < 0, axis=1)
    metrics["quantile_crossing_rate_raw"] = float(crossings.mean())
    metrics["quantile_negative_rate_raw"] = float((raw_matrix < 0).any(axis=1).mean())

    for index, alpha in enumerate(QUANTILES):
        raw_pred = raw_matrix[:, index]
        deployed_pred = rearranged[:, index]
        key = f"q{int(alpha*100):02d}"
        metrics[f"{key}_pinball_raw"] = pinball_loss(y_val.values, raw_pred, alpha)
        metrics[f"{key}_coverage_raw"] = float((y_val.values <= raw_pred).mean())
        metrics[f"{key}_pinball"] = pinball_loss(y_val.values, deployed_pred, alpha)
        metrics[f"{key}_coverage"] = float((y_val.values <= deployed_pred).mean())

    # Interval coverage diagnostics use the same rearranged bands served by
    # batch and live inference, so offline validation matches production.
    y_p10, y_p25, _, y_p75, y_p90 = rearranged.T

    metrics["coverage_80"] = float(((y_val.values >= y_p10) & (y_val.values <= y_p90)).mean())
    metrics["coverage_50"] = float(((y_val.values >= y_p25) & (y_val.values <= y_p75)).mean())
    metrics["interval_width_80_mean"] = float(np.mean(y_p90 - y_p10))
    metrics["interval_width_50_mean"] = float(np.mean(y_p75 - y_p25))

    return models, metrics


def run_training(horizons: List[int] = HORIZONS, tune: bool = False,
                 time_decay_years: float = 0.0, tune_trials: int = 30,
                 train_frac: float = 0.75, purge_days: int = 5):
    data_dir = get_data_dir()
    ml_dir = data_dir / "ml_training"
    models_dir = data_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    if time_decay_years > 0:
        logger.info(f"⚖️  Time-decay sample weighting enabled: half-life = {time_decay_years} years")

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
        # Sidecar columns prefixed with __ aren't features — they hold metadata
        # the trainer needs (currently: __earnings_date for time-decay weights).
        meta_cols = [c for c in df.columns if c.startswith("__")]
        feature_cols = [c for c in df.columns if c != target_col and c not in meta_cols]

        train_df, val_df, split_metadata = chronological_train_val_split(
            df,
            train_frac=train_frac,
            purge_days=purge_days,
        )
        X_train = train_df[feature_cols]
        X_val = val_df[feature_cols]
        y_train = train_df[target_col]
        y_val = val_df[target_col]
        earnings_date_train = pd.to_datetime(train_df["__earnings_date"])

        logger.info(f"Train: {len(X_train)} | Val: {len(X_val)} | Features: {len(feature_cols)}")
        logger.info(
            "Split: %s to %s | %s purged rows | validation %s to %s",
            split_metadata["train_start"],
            split_metadata["train_end"],
            split_metadata["rows_purged"],
            split_metadata["validation_start"],
            split_metadata["validation_end"],
        )
        logger.info(f"Target — train μ={y_train.mean():.4f} σ={y_train.std():.4f} | "
                     f"val μ={y_val.mean():.4f} σ={y_val.std():.4f}")

        # Build training sample weights via exponential time decay.
        # weight = exp(-days_old / (365 * half_life_years))
        # Recent rows weight ≈ 1; rows one half-life back weight ≈ 0.5; etc.
        sample_weight = None
        if time_decay_years > 0:
            ed_train = earnings_date_train.reset_index(drop=True)
            max_date = ed_train.max()
            days_old = (max_date - ed_train).dt.days.clip(lower=0).to_numpy()
            sample_weight = np.exp(-days_old / (365.0 * time_decay_years))
            logger.info(
                f"  decay weights: min={sample_weight.min():.4f} "
                f"max={sample_weight.max():.4f} "
                f"effective_n={sample_weight.sum():.0f} (raw n={len(sample_weight)})"
            )

        # ── Optional hyperparameter tuning ──
        best_params: Dict[str, Any] | None = None
        if tune:
            logger.info(f"  Tuning T-{horizon} with Optuna ({tune_trials} trials)…")
            best_params = tune_hyperparameters(
                X_train, y_train, X_val, y_val, horizon,
                n_trials=tune_trials, sample_weight=sample_weight,
            )
            logger.info(f"  Best params: {best_params}")

        # ── Point model ──
        model, metrics = train_point_model(
            X_train, y_train, X_val, y_val, horizon, sample_weight, params=best_params,
        )

        logger.info(f"Point model — MAE: {metrics['val_mae']:.4f}  "
                     f"RMSE: {metrics['val_rmse']:.4f}  R²: {metrics['val_r2']:.4f}")
        if "improvement_vs_straddle" in metrics:
            logger.info(f"  vs straddle: {metrics['improvement_vs_straddle']}")
        logger.info(f"  Top features: {list(metrics['feature_importance'].keys())[:5]}")

        # ── Quantile models ──
        q_models, q_metrics = train_quantile_models(
            X_train, y_train, X_val, y_val, horizon, sample_weight, params=best_params,
        )

        logger.info(f"Quantile models — 80% coverage: {q_metrics['coverage_80']:.1%} (target: 80%)  "
                     f"50% coverage: {q_metrics['coverage_50']:.1%} (target: 50%)")
        logger.info(f"  80% interval width: {q_metrics['interval_width_80_mean']:.4f}  "
                     f"50% interval width: {q_metrics['interval_width_50_mean']:.4f}")

        # ── Save point model ──
        model_path = models_dir / f"lgbm_T{horizon}.txt"
        save_native_model(model, model_path)

        # ── Save quantile models ──
        for alpha, qm in q_models.items():
            q_path = models_dir / f"lgbm_T{horizon}_q{int(alpha*100):02d}.txt"
            save_native_model(qm, q_path)

        # ── Save combined metadata ──
        all_metrics = {**metrics, **q_metrics}
        all_metrics["trained_at"] = datetime.now().isoformat()
        all_metrics["model_path"] = str(model_path)
        all_metrics["feature_cols"] = feature_cols
        all_metrics["quantiles"] = QUANTILES
        all_metrics["version"] = "v3"
        all_metrics["time_decay_years"] = time_decay_years if time_decay_years > 0 else None
        all_metrics["tuned"] = bool(best_params)
        all_metrics["best_params"] = best_params
        all_metrics["validation_split"] = split_metadata

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
    parser.add_argument(
        "--tune",
        action="store_true",
        help=(
            "Run Optuna hyperparameter search per horizon. ~30 trials × ~5s each "
            "= ~3 min/horizon, ~18 min total for 6 horizons. Best params stored "
            "in metadata.json under 'best_params'."
        ),
    )
    parser.add_argument(
        "--tune-trials",
        type=int,
        default=30,
        help="Optuna trials per horizon when --tune is set (default 30).",
    )
    parser.add_argument(
        "--time-decay",
        type=float,
        default=0.0,
        help=(
            "Half-life in years for exponential sample-weight decay. 0 = off. "
            "Recommended starting value: 1 (rows one year old weigh half). "
            "Affects training only — val MAE stays unweighted for fair comparison."
        ),
    )
    parser.add_argument(
        "--train-frac",
        type=float,
        default=0.75,
        help="Approximate chronological training fraction before date grouping (default 0.75).",
    )
    parser.add_argument(
        "--purge-days",
        type=int,
        default=5,
        help="Calendar-day embargo before validation begins (default 5).",
    )
    args = parser.parse_args()
    run_training(
        args.horizons,
        args.tune,
        time_decay_years=args.time_decay,
        tune_trials=args.tune_trials,
        train_frac=args.train_frac,
        purge_days=args.purge_days,
    )


if __name__ == "__main__":
    main()
