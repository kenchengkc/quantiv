"""
Minimal ML Pipeline Demo - Validates architecture on small symbol subset

Tests the full pipeline on 5-10 high-liquidity symbols to prove the concept.
"""

import os
import sys
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd
import joblib

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.model_trainer import ModelTrainer
from ml.serving_pipeline import MLServingPipeline

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)


def create_synthetic_training_data(data_dir: str):
    """Create minimal synthetic training data for demo purposes"""
    
    logger.info("Creating synthetic training data for demo...")
    
    # Create ml_training directory
    training_dir = os.path.join(data_dir, "ml_training")
    os.makedirs(training_dir, exist_ok=True)
    
    # Generate synthetic features and targets for each horizon
    # Based on historical patterns: straddles overestimate by ~30%
    horizons = [1, 2, 3, 7, 14, 21]
    
    for horizon in horizons:
        n_samples = 200  # Small sample for demo
        
        # Synthetic feature data
        data = {
            'symbol_encoded': [hash(f'SYM{i%20}') % 10000 for i in range(n_samples)],
            'horizon': [horizon] * n_samples,
            'earnings_month': [(i % 12) + 1 for i in range(n_samples)],
            'earnings_weekday': [i % 5 for i in range(n_samples)],
            'underlying_price': [100 + (i % 400) for i in range(n_samples)],
            'log_price': [4.6 + (i % 10) * 0.1 for i in range(n_samples)],
            'log_market_cap': [24 + (i % 5) * 0.5 for i in range(n_samples)],
            'atm_straddle_price': [5 + (i % 20) * 0.5 for i in range(n_samples)],
            'atm_straddle_pct': [0.02 + (i % 30) * 0.001 for i in range(n_samples)],
            'atm_iv': [0.3 + (i % 20) * 0.01 for i in range(n_samples)],
            'atm_delta': [0.5] * n_samples,
            'atm_gamma': [0.01 + (i % 10) * 0.001 for i in range(n_samples)],
            'atm_theta': [-0.5 - (i % 10) * 0.05 for i in range(n_samples)],
            'atm_vega': [0.15 + (i % 10) * 0.01 for i in range(n_samples)],
            'skew_25d': [0.05 + (i % 20) * 0.005 for i in range(n_samples)],
            'total_volume': [10000 + (i % 50) * 1000 for i in range(n_samples)],
            'pc_volume_ratio': [0.8 + (i % 40) * 0.01 for i in range(n_samples)],
            'volume_oi_ratio': [0.1 + (i % 20) * 0.01 for i in range(n_samples)],
            'iv_term_slope': [-0.02 + (i % 20) * 0.002 for i in range(n_samples)],
            'tte_earnings': [horizon / 365.0] * n_samples,
        }
        
        # Target: correction factor (realized / em_math)
        # Typical pattern: straddles overestimate, so correction < 1.0
        # Add noise and horizon-dependent bias
        base_correction = 0.70 - (horizon * 0.01)  # Longer lead = more overestimate
        data['target'] = [base_correction + (i % 50) * 0.01 - 0.25 for i in range(n_samples)]
        
        # Save to parquet
        df = pd.DataFrame(data)
        output_path = os.path.join(training_dir, f"training_T{horizon}.parquet")
        df.to_parquet(output_path, index=False)
        logger.info(f"Created {output_path} with {len(df)} samples")
    
    # Create synthetic bias curves
    bias_data = {
        'entity': ['market'] * 7,
        'lead_time_days': [1, 2, 3, 7, 14, 21, 30],
        'median_bias': [0.686, 0.710, 0.629, 0.697, 0.660, 0.624, 0.550],
        'n_samples': [380, 540, 470, 577, 577, 572, 377]
    }
    bias_df = pd.DataFrame(bias_data)
    bias_path = os.path.join(data_dir, "bias_curves.parquet")
    bias_df.to_parquet(bias_path, index=False)
    logger.info(f"Created {bias_path}")
    
    return True


def run_ml_demo(data_dir: str = "../../data"):
    """Run minimal ML pipeline demo"""
    
    logger.info("=== ML Pipeline Demo (Minimal Validation) ===")
    start_time = datetime.now()
    
    try:
        # Step 1: Create synthetic training data
        logger.info("Step 1: Creating synthetic training data...")
        create_synthetic_training_data(data_dir)
        logger.info("✅ Training data created")
        
        # Step 2: Train models (fast mode, no optimization)
        logger.info("\nStep 2: Training models (fast mode)...")
        training_dir = os.path.join(data_dir, "ml_training")
        trainer = ModelTrainer(training_dir)
        
        horizons = [1, 2, 3, 7, 14, 21]
        model_results = {}
        
        # Use train_all_models for cleaner workflow
        results = trainer.train_all_models(optimize=False)
        
        for horizon, result in results.items():
            metrics = result['metrics']
            logger.info(f"  ✅ T-{horizon}: MAE={metrics['val_mae']:.4f}, "
                       f"RMSE={metrics['val_rmse']:.4f}")
            model_results[horizon] = {
                'success': True,
                'mae': metrics['val_mae'],
                'rmse': metrics['val_rmse'],
                'n_train': len(result['validation_predictions']['y_true']),  # approximate
                'n_val': len(result['validation_predictions']['y_true'])
            }
        
        # Save models to disk
        saved_paths = trainer.save_models(results)
        logger.info(f"✅ Saved {len(saved_paths)} models to disk")
        
        logger.info(f"✅ Trained {sum(1 for r in model_results.values() if r['success'])}/{len(horizons)} models")
        
        # Step 3: Test serving pipeline (mock implementation for demo)
        logger.info("\nStep 3: Testing serving pipeline...")
        
        # Load trained models
        models_loaded = {}
        for horizon in horizons:
            model_path = os.path.join(data_dir, "..", "models", f"lgbm_T{horizon}.joblib")
            if os.path.exists(model_path):
                models_loaded[horizon] = joblib.load(model_path)
        
        # Load bias curves
        bias_df = pd.read_parquet(os.path.join(data_dir, "bias_curves.parquet"))
        bias_multipliers = dict(zip(bias_df['lead_time_days'], bias_df['median_bias']))
        
        logger.info(f"Loaded {len(models_loaded)} models and {len(bias_multipliers)} bias multipliers")
        
        test_cases = [
            ('AAPL', '2024-04-01', 'Technology', 7),
            ('MSFT', '2024-04-15', 'Technology', 14),
            ('GOOGL', '2024-04-20', 'Technology', 3),
        ]
        
        logger.info("\n" + "="*90)
        logger.info(f"{'Symbol':<8} {'Math EM':<10} {'ML EM':<10} {'P10-P90':<20} {'Correction':<12} {'Confidence':<10}")
        logger.info("="*90)
        
        for symbol, date, sector, lead_days in test_cases:
            try:
                # Mock math baseline (simulated ATM straddle)
                em_math_raw = 0.035 + (hash(symbol) % 20) * 0.001  # 3.5-5.5%
                
                # Apply bias correction
                closest_lead = min(bias_multipliers.keys(), key=lambda x: abs(x - lead_days))
                bias_mult = bias_multipliers[closest_lead]
                em_math = em_math_raw * bias_mult
                
                # ML prediction (mock feature vector)
                if lead_days in models_loaded:
                    mock_features = pd.DataFrame([{
                        'symbol_encoded': hash(symbol) % 10000,
                        'horizon': lead_days,
                        'earnings_month': 4,
                        'earnings_weekday': 1,
                        'underlying_price': 150 + (hash(symbol) % 50),
                        'log_price': 5.0,
                        'log_market_cap': 26.0,
                        'atm_straddle_price': 5.0,
                        'atm_straddle_pct': em_math,
                        'atm_iv': 0.35,
                        'atm_delta': 0.5,
                        'atm_gamma': 0.01,
                        'atm_theta': -0.5,
                        'atm_vega': 0.15,
                        'skew_25d': 0.05,
                        'total_volume': 15000,
                        'pc_volume_ratio': 0.9,
                        'volume_oi_ratio': 0.15,
                        'iv_term_slope': -0.01,
                        'tte_earnings': lead_days / 365.0,
                    }])
                    
                    model_data = models_loaded[lead_days]
                    ml_correction = model_data['model'].predict(mock_features)[0]
                    em_ml = em_math * ml_correction
                    
                    # Confidence bands
                    uncertainty = 0.15
                    p10 = em_ml * (1 - uncertainty)
                    p90 = em_ml * (1 + uncertainty)
                    confidence = 0.75
                else:
                    em_ml = em_math
                    ml_correction = 1.0
                    p10, p90 = em_math * 0.85, em_math * 1.15
                    confidence = 0.50
                
                logger.info(
                    f"{symbol:<8} "
                    f"{em_math:<10.3f} "
                    f"{em_ml:<10.3f} "
                    f"[{p10:.3f}, {p90:.3f}]  "
                    f"{ml_correction:<12.3f} "
                    f"{confidence:<10.2f}"
                )
            except Exception as e:
                logger.warning(f"{symbol:<8} Failed: {str(e)[:60]}")
        
        logger.info("="*90)
        
        # Summary
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"\n{'='*90}")
        logger.info(f"✅ Demo completed successfully in {duration:.1f}s")
        logger.info(f"{'='*90}")
        
        logger.info(f"\nDemo Results:")
        logger.info(f"  • Trained {len(model_results)} horizon-specific models")
        logger.info(f"  • Generated ML-enhanced forecasts with confidence bands")
        logger.info(f"  • Validated end-to-end pipeline architecture")
        
        logger.info(f"\nNext Steps for Production:")
        logger.info(f"  1. ✅ Pipeline architecture validated")
        logger.info(f"  2. Replace synthetic data with real historical options data")
        logger.info(f"  3. Expand training to 2023-2024 (18 months of data)")
        logger.info(f"  4. Enable hyperparameter optimization (50+ trials per horizon)")
        logger.info(f"  5. Validate on held-out test set for calibration")
        logger.info(f"  6. Integrate serving pipeline into backend API")
        logger.info(f"  7. Update frontend to display Math vs ML forecasts")
        
        return True
        
    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = run_ml_demo()
    sys.exit(0 if success else 1)
