#!/usr/bin/env python3
"""
LightGBM Model Training for Multi-Horizon Expected Move Prediction
Trains separate models for each lead time horizon (T-21, T-14, T-7, T-3, T-2, T-1)
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import logging
import json
import joblib
import warnings
warnings.filterwarnings('ignore')

# ML imports
from lightgbm import LGBMRegressor
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler
from sklearn.isotonic import IsotonicRegression
import optuna

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ModelTrainer:
    """Train LightGBM models for expected move prediction"""
    
    def __init__(self, data_dir: str = "data/ml_training", models_dir: str = "models"):
        self.data_dir = Path(data_dir)
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        
        self.horizons = [1, 2, 3, 7, 14, 21]
        self.trained_models = {}
        self.feature_importance = {}
        self.validation_scores = {}
    
    def load_training_data(self) -> Dict[int, pd.DataFrame]:
        """Load training data for all horizons"""
        
        training_data = {}
        
        for horizon in self.horizons:
            file_path = self.data_dir / f"training_T{horizon}.parquet"
            
            if file_path.exists():
                df = pd.read_parquet(file_path)
                logger.info(f"Loaded T-{horizon}: {len(df)} samples")
                training_data[horizon] = df
            else:
                logger.warning(f"Training data not found for T-{horizon}: {file_path}")
        
        return training_data
    
    def prepare_features(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """Prepare features and target for training"""
        
        # Separate features and target
        target_col = 'target'
        feature_cols = [col for col in df.columns if col != target_col]
        
        X = df[feature_cols].copy()
        y = df[target_col].copy()
        
        # Handle missing values
        X = X.fillna(X.median())
        
        # Remove infinite values
        X = X.replace([np.inf, -np.inf], np.nan).fillna(X.median())
        y = y.replace([np.inf, -np.inf], np.nan).fillna(y.median())
        
        # Remove outliers in target (beyond 3 std)
        target_mean = y.mean()
        target_std = y.std()
        outlier_mask = np.abs(y - target_mean) <= 3 * target_std
        
        X = X[outlier_mask]
        y = y[outlier_mask]
        
        logger.info(f"After preprocessing: {len(X)} samples, {len(feature_cols)} features")
        
        return X, y
    
    def optimize_hyperparameters(self, X: pd.DataFrame, y: pd.Series, 
                                horizon: int, n_trials: int = 50) -> Dict[str, Any]:
        """Optimize hyperparameters using Optuna"""
        
        def objective(trial):
            params = {
                'objective': 'regression',
                'metric': 'mae',
                'boosting_type': 'gbdt',
                'num_leaves': trial.suggest_int('num_leaves', 10, 300),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
                'feature_fraction': trial.suggest_float('feature_fraction', 0.4, 1.0),
                'bagging_fraction': trial.suggest_float('bagging_fraction', 0.4, 1.0),
                'bagging_freq': trial.suggest_int('bagging_freq', 1, 7),
                'min_child_samples': trial.suggest_int('min_child_samples', 5, 100),
                'reg_alpha': trial.suggest_float('reg_alpha', 0, 10),
                'reg_lambda': trial.suggest_float('reg_lambda', 0, 10),
                'random_state': 42,
                'verbose': -1
            }
            
            # Time series cross-validation
            tscv = TimeSeriesSplit(n_splits=3)
            model = LGBMRegressor(**params)
            
            scores = cross_val_score(model, X, y, cv=tscv, scoring='neg_mean_absolute_error')
            return -scores.mean()
        
        logger.info(f"Optimizing hyperparameters for T-{horizon} ({n_trials} trials)")
        
        study = optuna.create_study(direction='minimize')
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        
        best_params = study.best_params
        best_params.update({
            'objective': 'regression',
            'metric': 'mae',
            'boosting_type': 'gbdt',
            'random_state': 42,
            'verbose': -1
        })
        
        logger.info(f"T-{horizon} best MAE: {study.best_value:.4f}")
        
        return best_params
    
    def train_model(self, X: pd.DataFrame, y: pd.Series, 
                   horizon: int, optimize: bool = True) -> Dict[str, Any]:
        """Train model for specific horizon"""
        
        logger.info(f"Training model for T-{horizon}")
        
        # Split data chronologically (80/20 split)
        split_idx = int(0.8 * len(X))
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]
        
        logger.info(f"Train: {len(X_train)}, Validation: {len(X_val)}")
        
        # Optimize hyperparameters
        if optimize:
            best_params = self.optimize_hyperparameters(X_train, y_train, horizon)
        else:
            # Default parameters
            best_params = {
                'objective': 'regression',
                'metric': 'mae',
                'boosting_type': 'gbdt',
                'num_leaves': 100,
                'learning_rate': 0.1,
                'feature_fraction': 0.8,
                'bagging_fraction': 0.8,
                'bagging_freq': 5,
                'min_child_samples': 20,
                'reg_alpha': 0.1,
                'reg_lambda': 0.1,
                'random_state': 42,
                'verbose': -1
            }
        
        # Train final model
        model = LGBMRegressor(**best_params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[
                # Early stopping
                # LightGBM callback for early stopping
            ],
            eval_metric='mae'
        )
        
        # Predictions
        y_train_pred = model.predict(X_train)
        y_val_pred = model.predict(X_val)
        
        # Metrics
        train_mae = mean_absolute_error(y_train, y_train_pred)
        val_mae = mean_absolute_error(y_val, y_val_pred)
        train_rmse = np.sqrt(mean_squared_error(y_train, y_train_pred))
        val_rmse = np.sqrt(mean_squared_error(y_val, y_val_pred))
        
        # Feature importance
        feature_importance = dict(zip(X.columns, model.feature_importances_))
        
        # Calibration (isotonic regression for confidence intervals)
        calibrator = IsotonicRegression(out_of_bounds='clip')
        calibrator.fit(y_val_pred, y_val)
        
        results = {
            'model': model,
            'calibrator': calibrator,
            'hyperparameters': best_params,
            'feature_importance': feature_importance,
            'metrics': {
                'train_mae': train_mae,
                'val_mae': val_mae,
                'train_rmse': train_rmse,
                'val_rmse': val_rmse
            },
            'validation_predictions': {
                'y_true': y_val.values,
                'y_pred': y_val_pred
            }
        }
        
        logger.info(f"T-{horizon} - Train MAE: {train_mae:.4f}, Val MAE: {val_mae:.4f}")
        
        return results
    
    def train_all_models(self, optimize: bool = True) -> Dict[int, Dict[str, Any]]:
        """Train models for all horizons"""
        
        # Load training data
        training_data = self.load_training_data()
        
        if not training_data:
            logger.error("No training data available")
            return {}
        
        results = {}
        
        for horizon in self.horizons:
            if horizon not in training_data:
                logger.warning(f"No training data for T-{horizon}")
                continue
            
            try:
                # Prepare data
                X, y = self.prepare_features(training_data[horizon])
                
                if len(X) < 50:  # Minimum samples for training
                    logger.warning(f"Insufficient data for T-{horizon}: {len(X)} samples")
                    continue
                
                # Train model
                model_results = self.train_model(X, y, horizon, optimize)
                results[horizon] = model_results
                
                # Store for later use
                self.trained_models[horizon] = model_results['model']
                self.feature_importance[horizon] = model_results['feature_importance']
                self.validation_scores[horizon] = model_results['metrics']
                
            except Exception as e:
                logger.error(f"Failed to train model for T-{horizon}: {e}")
                continue
        
        return results
    
    def save_models(self, results: Dict[int, Dict[str, Any]]) -> Dict[int, str]:
        """Save trained models and metadata"""
        
        saved_paths = {}
        
        for horizon, model_results in results.items():
            try:
                # Model file path
                model_path = self.models_dir / f"lgbm_T{horizon}.joblib"
                
                # Save model and calibrator
                model_data = {
                    'model': model_results['model'],
                    'calibrator': model_results['calibrator'],
                    'hyperparameters': model_results['hyperparameters'],
                    'feature_names': list(model_results['feature_importance'].keys()),
                    'trained_at': datetime.now().isoformat()
                }
                
                joblib.dump(model_data, model_path)
                saved_paths[horizon] = str(model_path)
                
                # Save metadata
                metadata_path = self.models_dir / f"metadata_T{horizon}.json"
                metadata = {
                    'horizon': horizon,
                    'metrics': model_results['metrics'],
                    'feature_importance': model_results['feature_importance'],
                    'hyperparameters': model_results['hyperparameters'],
                    'trained_at': datetime.now().isoformat()
                }
                
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2, default=str)
                
                logger.info(f"Saved T-{horizon} model to {model_path}")
                
            except Exception as e:
                logger.error(f"Failed to save model for T-{horizon}: {e}")
        
        return saved_paths
    
    def generate_predictions(self, results: Dict[int, Dict[str, Any]], 
                           output_path: str = "data/ml_predictions.parquet") -> str:
        """Generate predictions for serving"""
        
        # This would typically generate predictions for current/upcoming earnings
        # For now, we'll create a placeholder structure
        
        predictions = []
        
        for horizon in results.keys():
            # Placeholder prediction
            predictions.append({
                'horizon': horizon,
                'model_type': 'lightgbm',
                'created_at': datetime.now(),
                'status': 'trained'
            })
        
        df = pd.DataFrame(predictions)
        df.to_parquet(output_path, index=False)
        
        logger.info(f"Saved predictions metadata to {output_path}")
        return output_path

def main():
    """Train all models"""
    
    trainer = ModelTrainer()
    
    # Train models (set optimize=False for faster training during development)
    results = trainer.train_all_models(optimize=True)
    
    if not results:
        logger.error("No models trained successfully")
        return
    
    # Save models
    saved_paths = trainer.save_models(results)
    
    # Generate predictions
    predictions_path = trainer.generate_predictions(results)
    
    # Print summary
    print("\n=== Model Training Summary ===")
    for horizon, model_results in results.items():
        metrics = model_results['metrics']
        print(f"T-{horizon}:")
        print(f"  Validation MAE: {metrics['val_mae']:.4f}")
        print(f"  Validation RMSE: {metrics['val_rmse']:.4f}")
        print(f"  Model saved: {saved_paths.get(horizon, 'Failed')}")
        
        # Top features
        importance = model_results['feature_importance']
        top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"  Top features: {', '.join([f[0] for f in top_features])}")

if __name__ == "__main__":
    main()
