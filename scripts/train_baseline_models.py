#!/usr/bin/env python3
"""
Train baseline expected move models for Quantiv ML pipeline.

Implements three baseline approaches:
1. Linear regression on key volatility features
2. Gradient boosting (LightGBM) with comprehensive features
3. Heuristic model (proportional to pre-earnings IV)

Usage:
  python scripts/train_baseline_models.py [--duckdb-path /srv/quantiv-data/quantiv.duckdb]
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import pandas as pd
import numpy as np
import pickle
import json
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import lightgbm as lgb
import warnings
warnings.filterwarnings('ignore')

def load_training_data(conn):
    """Load training data from DuckDB."""
    print("[data] Loading training data from em_training view...")
    
    query = """
    SELECT 
      act_symbol,
      earnings_date,
      -- Target variable
      y,
      -- Volatility features
      iv_t1,
      hv_t1,
      iv_week_ago,
      iv_month_ago,
      iv_hv_spread,
      iv_percentile_est,
      -- Options features
      avg_iv_t1,
      atm_iv_t1,
      iv_skew,
      avg_gamma_t1,
      avg_vega_t1,
      call_put_ratio,
      total_contracts,
      -- Derived features
      CASE WHEN iv_t1 IS NOT NULL AND hv_t1 IS NOT NULL 
           THEN iv_t1 / NULLIF(hv_t1, 0) ELSE NULL END as iv_hv_ratio,
      CASE WHEN atm_iv_t1 IS NOT NULL AND iv_t1 IS NOT NULL 
           THEN atm_iv_t1 / NULLIF(iv_t1, 0) ELSE NULL END as atm_iv_ratio
    FROM em_training
    WHERE y IS NOT NULL
      AND y > 0  -- Positive realized moves only
      AND y < 2.0  -- Filter extreme outliers (>200% moves)
      AND iv_t1 IS NOT NULL
      AND iv_t1 > 0
    ORDER BY earnings_date
    """
    
    df = pd.read_sql_query(query, conn)
    print(f"[data] Loaded {len(df)} training samples")
    print(f"[data] Date range: {df['earnings_date'].min()} to {df['earnings_date'].max()}")
    print(f"[data] Unique symbols: {df['act_symbol'].nunique()}")
    
    return df

def prepare_features(df):
    """Prepare feature sets for different models."""
    print("[features] Preparing feature sets...")
    
    # Basic features for linear regression
    basic_features = [
        'iv_t1', 'hv_t1', 'iv_hv_spread', 'iv_hv_ratio'
    ]
    
    # Extended features for gradient boosting
    extended_features = [
        'iv_t1', 'hv_t1', 'iv_week_ago', 'iv_month_ago', 
        'iv_hv_spread', 'iv_percentile_est', 'iv_hv_ratio',
        'avg_iv_t1', 'atm_iv_t1', 'iv_skew', 'atm_iv_ratio',
        'avg_gamma_t1', 'avg_vega_t1', 'call_put_ratio',
        'total_contracts'
    ]
    
    # Create feature matrices
    X_basic = df[basic_features].copy()
    X_extended = df[extended_features].copy()
    
    # Fill missing values with median
    for col in X_basic.columns:
        X_basic[col] = X_basic[col].fillna(X_basic[col].median())
    
    for col in X_extended.columns:
        X_extended[col] = X_extended[col].fillna(X_extended[col].median())
    
    y = df['y'].values
    dates = pd.to_datetime(df['earnings_date'])
    
    print(f"[features] Basic features: {len(basic_features)}")
    print(f"[features] Extended features: {len(extended_features)}")
    
    return X_basic, X_extended, y, dates, basic_features, extended_features

def create_time_splits(dates, n_splits=5):
    """Create time-based train/validation splits."""
    print(f"[splits] Creating {n_splits} time-based splits...")
    
    # Sort by date and create splits
    sorted_indices = np.argsort(dates)
    n_samples = len(dates)
    
    splits = []
    for i in range(n_splits):
        # Use expanding window: train on all data up to split point
        split_point = int(n_samples * (0.6 + 0.08 * i))  # 60%, 68%, 76%, 84%, 92%
        
        train_idx = sorted_indices[:split_point]
        val_idx = sorted_indices[split_point:split_point + int(n_samples * 0.15)]
        
        if len(val_idx) > 10:  # Ensure minimum validation size
            splits.append((train_idx, val_idx))
    
    print(f"[splits] Created {len(splits)} valid splits")
    return splits

def train_linear_model(X, y, dates, features):
    """Train linear regression baseline."""
    print("[model] Training linear regression baseline...")
    
    splits = create_time_splits(dates)
    scaler = StandardScaler()
    
    cv_scores = []
    models = []
    scalers = []
    
    for fold, (train_idx, val_idx) in enumerate(splits):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]
        
        # Scale features
        fold_scaler = StandardScaler()
        X_train_scaled = fold_scaler.fit_transform(X_train)
        X_val_scaled = fold_scaler.transform(X_val)
        
        # Train model
        model = LinearRegression()
        model.fit(X_train_scaled, y_train)
        
        # Predict and evaluate
        y_pred = model.predict(X_val_scaled)
        mae = mean_absolute_error(y_val, y_pred)
        rmse = np.sqrt(mean_squared_error(y_val, y_pred))
        r2 = r2_score(y_val, y_pred)
        
        cv_scores.append({'fold': fold, 'mae': mae, 'rmse': rmse, 'r2': r2})
        models.append(model)
        scalers.append(fold_scaler)
        
        print(f"  Fold {fold}: MAE={mae:.4f}, RMSE={rmse:.4f}, R²={r2:.4f}")
    
    # Train final model on all data
    X_scaled = scaler.fit_transform(X)
    final_model = LinearRegression()
    final_model.fit(X_scaled, y)
    
    # Feature importance (absolute coefficients)
    importance = np.abs(final_model.coef_)
    feature_importance = dict(zip(features, importance))
    
    results = {
        'model_type': 'linear_regression',
        'cv_scores': cv_scores,
        'avg_mae': np.mean([s['mae'] for s in cv_scores]),
        'avg_rmse': np.mean([s['rmse'] for s in cv_scores]),
        'avg_r2': np.mean([s['r2'] for s in cv_scores]),
        'feature_importance': feature_importance,
        'model': final_model,
        'scaler': scaler,
        'features': features
    }
    
    print(f"[model] Linear regression CV results:")
    print(f"  Average MAE: {results['avg_mae']:.4f}")
    print(f"  Average RMSE: {results['avg_rmse']:.4f}")
    print(f"  Average R²: {results['avg_r2']:.4f}")
    
    return results

def train_lgb_model(X, y, dates, features):
    """Train LightGBM gradient boosting model."""
    print("[model] Training LightGBM gradient boosting model...")
    
    splits = create_time_splits(dates)
    
    cv_scores = []
    models = []
    feature_importances = []
    
    # LightGBM parameters
    lgb_params = {
        'objective': 'regression',
        'metric': 'mae',
        'boosting_type': 'gbdt',
        'num_leaves': 31,
        'learning_rate': 0.05,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'verbose': -1,
        'random_state': 42
    }
    
    for fold, (train_idx, val_idx) in enumerate(splits):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]
        
        # Create LightGBM datasets
        train_data = lgb.Dataset(X_train, label=y_train)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
        
        # Train model
        model = lgb.train(
            lgb_params,
            train_data,
            valid_sets=[val_data],
            num_boost_round=1000,
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)]
        )
        
        # Predict and evaluate
        y_pred = model.predict(X_val, num_iteration=model.best_iteration)
        mae = mean_absolute_error(y_val, y_pred)
        rmse = np.sqrt(mean_squared_error(y_val, y_pred))
        r2 = r2_score(y_val, y_pred)
        
        cv_scores.append({'fold': fold, 'mae': mae, 'rmse': rmse, 'r2': r2})
        models.append(model)
        feature_importances.append(model.feature_importance())
        
        print(f"  Fold {fold}: MAE={mae:.4f}, RMSE={rmse:.4f}, R²={r2:.4f}")
    
    # Train final model on all data
    train_data = lgb.Dataset(X, label=y)
    final_model = lgb.train(
        lgb_params,
        train_data,
        num_boost_round=1000,
        callbacks=[lgb.log_evaluation(0)]
    )
    
    # Average feature importance across folds
    avg_importance = np.mean(feature_importances, axis=0)
    feature_importance = dict(zip(features, avg_importance))
    
    results = {
        'model_type': 'lightgbm',
        'cv_scores': cv_scores,
        'avg_mae': np.mean([s['mae'] for s in cv_scores]),
        'avg_rmse': np.mean([s['rmse'] for s in cv_scores]),
        'avg_r2': np.mean([s['r2'] for s in cv_scores]),
        'feature_importance': feature_importance,
        'model': final_model,
        'features': features,
        'params': lgb_params
    }
    
    print(f"[model] LightGBM CV results:")
    print(f"  Average MAE: {results['avg_mae']:.4f}")
    print(f"  Average RMSE: {results['avg_rmse']:.4f}")
    print(f"  Average R²: {results['avg_r2']:.4f}")
    
    return results

def train_heuristic_model(df):
    """Train simple heuristic model (proportional to IV)."""
    print("[model] Training heuristic model...")
    
    # Simple heuristic: realized_move = alpha * iv_t1
    # Find optimal alpha using least squares
    iv_values = df['iv_t1'].values
    y_values = df['y'].values
    
    # Remove any remaining NaN values
    mask = ~(np.isnan(iv_values) | np.isnan(y_values)) & (iv_values > 0)
    iv_clean = iv_values[mask]
    y_clean = y_values[mask]
    
    # Optimal alpha = (X^T * y) / (X^T * X) for simple linear model
    alpha = np.sum(iv_clean * y_clean) / np.sum(iv_clean ** 2)
    
    # Evaluate on all data
    y_pred = alpha * iv_clean
    mae = mean_absolute_error(y_clean, y_pred)
    rmse = np.sqrt(mean_squared_error(y_clean, y_pred))
    r2 = r2_score(y_clean, y_pred)
    
    results = {
        'model_type': 'heuristic',
        'alpha': alpha,
        'mae': mae,
        'rmse': rmse,
        'r2': r2,
        'n_samples': len(y_clean)
    }
    
    print(f"[model] Heuristic model results:")
    print(f"  Optimal alpha: {alpha:.4f}")
    print(f"  MAE: {mae:.4f}")
    print(f"  RMSE: {rmse:.4f}")
    print(f"  R²: {r2:.4f}")
    
    return results

def save_models(models_dict, models_dir):
    """Save trained models and results."""
    print(f"[save] Saving models to {models_dir}")
    
    models_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for model_name, results in models_dict.items():
        # Save model artifact
        model_path = models_dir / f"em_model_{model_name}_{timestamp}.pkl"
        with open(model_path, 'wb') as f:
            pickle.dump(results, f)
        print(f"[save] Saved {model_name} model: {model_path}")
        
        # Save results summary
        summary = {
            'model_type': results['model_type'],
            'timestamp': timestamp,
            'performance': {
                'mae': results.get('avg_mae', results.get('mae')),
                'rmse': results.get('avg_rmse', results.get('rmse')),
                'r2': results.get('avg_r2', results.get('r2'))
            }
        }
        
        if 'feature_importance' in results:
            # Sort features by importance
            importance = results['feature_importance']
            sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)
            summary['top_features'] = sorted_features[:10]
        
        summary_path = models_dir / f"em_model_{model_name}_{timestamp}_summary.json"
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"[save] Saved {model_name} summary: {summary_path}")
    
    # Save best model as latest
    best_model = min(models_dict.items(), key=lambda x: x[1].get('avg_mae', x[1].get('mae')))
    best_name, best_results = best_model
    
    latest_path = models_dir / "em_model_latest.pkl"
    with open(latest_path, 'wb') as f:
        pickle.dump(best_results, f)
    print(f"[save] Saved best model ({best_name}) as latest: {latest_path}")
    
    return best_name, best_results

def main():
    parser = argparse.ArgumentParser(description="Train baseline EM models for Quantiv")
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=Path("/srv/quantiv-data/quantiv.duckdb"),
        help="Path to DuckDB file"
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="Directory to save models (default: {data-root}/models)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick training with fewer CV folds"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        duckdb_path = project_root / "data" / "quantiv.duckdb"
        models_dir = project_root / "data" / "models"
    else:
        duckdb_path = args.duckdb_path
        models_dir = args.models_dir or (duckdb_path.parent / "models")
    
    if not duckdb_path.exists():
        print(f"[error] DuckDB file not found: {duckdb_path}")
        print("Run build_em_labels_features.py first to create training data.")
        sys.exit(1)
    
    print(f"[setup] Training baseline EM models")
    print(f"[setup] DuckDB: {duckdb_path}")
    print(f"[setup] Models dir: {models_dir}")
    print(f"[setup] Timestamp: {datetime.now().isoformat()}")
    
    try:
        # Load data
        conn = duckdb.connect(str(duckdb_path))
        df = load_training_data(conn)
        conn.close()
        
        if len(df) < 50:
            print(f"[error] Insufficient training data: {len(df)} samples")
            print("Need at least 50 samples for reliable training.")
            sys.exit(1)
        
        # Prepare features
        X_basic, X_extended, y, dates, basic_features, extended_features = prepare_features(df)
        
        # Train models
        models = {}
        
        # 1. Linear regression
        models['linear'] = train_linear_model(X_basic, y, dates, basic_features)
        
        # 2. LightGBM (if enough data)
        if len(df) >= 100:
            models['lgb'] = train_lgb_model(X_extended, y, dates, extended_features)
        else:
            print("[model] Skipping LightGBM (insufficient data)")
        
        # 3. Heuristic
        models['heuristic'] = train_heuristic_model(df)
        
        # Save models
        best_name, best_results = save_models(models, models_dir)
        
        print(f"\n[success] Model training completed")
        print(f"[success] Best model: {best_name}")
        print(f"[success] Best MAE: {best_results.get('avg_mae', best_results.get('mae')):.4f}")
        
    except Exception as e:
        print(f"[error] Model training failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
