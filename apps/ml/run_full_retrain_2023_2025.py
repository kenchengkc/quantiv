#!/usr/bin/env python3
"""
Full Retrain: Train models on ALL available data (2023-2025)
This maximizes model learning for production deployment
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.bias_curve_builder import BiaseCurveBuilder
from ml.feature_engineering import FeatureEngineer
from ml.model_trainer import ModelTrainer
from ml.serving_pipeline import MLServingPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

def run_full_retrain(
    data_dir: str = "../../data",
    n_trials: int = 50,
    train_start: str = "2023-01-01",
    train_end: str = "2025-08-15"
):
    """
    Train production models on ALL available data
    
    Previous training: 2023-01-01 to 2024-06-30 (18 months)
    New training:      2023-01-01 to 2025-08-15 (32 months!)
    
    Benefits:
    - 78% more training data
    - Models learn recent 2024-2025 market conditions
    - Better calibrated to current volatility regime
    """
    start_time = datetime.now()
    
    logger.info("=" * 80)
    logger.info("FULL RETRAIN ON ALL AVAILABLE DATA (2023-2025)")
    logger.info("=" * 80)
    logger.info(f"Training period: {train_start} to {train_end}")
    logger.info(f"Duration: ~32 months of data")
    logger.info(f"Optuna trials per model: {n_trials}")
    logger.info("")
    logger.info("📊 This will include:")
    logger.info("  - Original 18 months (2023-2024 H1)")
    logger.info("  - New 14 months (2024 H2 - 2025 Aug)")
    logger.info("  - 78% more training samples!")
    logger.info("")
    
    try:
        # Step 1: Bias Curves (use cached if available)
        logger.info("Step 1: Loading/rebuilding bias curves...")
        bias_curve_path = os.path.join(data_dir, "bias_curves.parquet")
        
        # Rebuild bias curves with full dataset
        logger.info("Rebuilding bias curves with full 2023-2025 data...")
        builder = BiaseCurveBuilder(data_dir)
        
        # Extract historical bias points
        bias_points = builder.extract_historical_bias_points(
            start_date=train_start,
            end_date=train_end
        )
        
        if bias_points:
            logger.info(f"Extracted {len(bias_points)} historical bias points")
            # Build bias curves from points
            bias_curves = builder.build_bias_curves(bias_points)
            # Save to disk
            builder.save_bias_curves(bias_curves, bias_curve_path)
            logger.info(f"✅ Bias curves rebuilt with full historical data")
        else:
            logger.warning("No bias points extracted, using existing curves if available")
        
        # Step 2: Feature Engineering on FULL dataset
        logger.info(f"\nStep 2: Feature engineering on FULL dataset ({train_start} to {train_end})...")
        engineer = FeatureEngineer(data_dir)
        feature_sets = engineer.extract_training_data(train_start, train_end)
        
        if not feature_sets:
            logger.error("No training data extracted - pipeline cannot continue")
            return False
        
        training_paths = engineer.save_training_data(feature_sets)
        
        # Log training data statistics
        logger.info(f"\n✅ Training data extracted for {len(feature_sets)} horizons:")
        total_samples = 0
        for horizon, feature_set in sorted(feature_sets.items()):
            samples = len(feature_set.features)
            total_samples += samples
            logger.info(f"  T-{horizon}: {samples} samples, "
                       f"{len(feature_set.features.columns)} features")
        
        logger.info(f"\n📈 Total training samples: {total_samples:,}")
        logger.info(f"🎯 Expected improvement from previous 18-month training")
        
        # Step 3: Hyperparameter Optimization & Training
        logger.info(f"\nStep 3: Training models with hyperparameter optimization...")
        logger.info(f"Using Optuna with {n_trials} trials per model")
        logger.info("⏱️  Estimated time: 5-10 minutes per model (30-60 min total)\n")
        
        training_dir = os.path.join(data_dir, "ml_training")
        trainer = ModelTrainer(training_dir)
        
        # Train with optimization enabled
        model_results = trainer.train_all_models(optimize=True)
        
        if not model_results:
            logger.error("Model training failed")
            return False
        
        # Log optimization results
        logger.info(f"\n✅ Optimization completed for {len(model_results)} models:")
        for horizon, result in sorted(model_results.items()):
            metrics = result['metrics']
            params = result['hyperparameters']
            logger.info(f"\nT-{horizon}:")
            logger.info(f"  Val MAE:  {metrics['val_mae']:.4f}")
            logger.info(f"  Val RMSE: {metrics['val_rmse']:.4f}")
            logger.info(f"  Best params: learning_rate={params.get('learning_rate', 'N/A'):.3f}, "
                       f"num_leaves={params.get('num_leaves', 'N/A')}")
        
        # Save optimized models
        saved_paths = trainer.save_models(model_results)
        logger.info(f"\n✅ Saved {len(saved_paths)} optimized models to production directory")
        
        # Step 4: Validation
        logger.info("\nStep 4: Validating serving pipeline...")
        serving = MLServingPipeline(data_dir)
        
        logger.info(f"✅ Serving pipeline loaded:")
        logger.info(f"  - {len(serving.models)} models")
        logger.info(f"  - {len(serving.bias_curves)} bias curve entities")
        
        # Summary
        duration = (datetime.now() - start_time).total_seconds()
        logger.info("\n" + "=" * 80)
        logger.info(f"✅ FULL RETRAIN COMPLETED in {duration:.1f}s")
        logger.info("=" * 80)
        
        logger.info("\n📊 Training Summary:")
        logger.info(f"  Period: {train_start} to {train_end} (32 months)")
        logger.info(f"  Total samples: {total_samples:,}")
        logger.info(f"  Optimization: {n_trials} trials per model")
        logger.info(f"  Models trained: {len(model_results)}")
        
        logger.info("\n✅ Production Models Ready:")
        for horizon in sorted(model_results.keys()):
            metrics = model_results[horizon]['metrics']
            logger.info(f"  T-{horizon}: MAE={metrics['val_mae']:.4f}, RMSE={metrics['val_rmse']:.4f}")
        
        logger.info("\n🚀 Next Steps:")
        logger.info("  1. Restart backend to load new models")
        logger.info("  2. Models will automatically serve predictions")
        logger.info("  3. Monitor performance vs realized moves")
        logger.info("  4. Consider monthly retraining schedule")
        
        return True
        
    except Exception as e:
        logger.error(f"Pipeline failed with error: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Full Retrain on 2023-2025 Data")
    parser.add_argument("--data-dir", default="../../data", help="Data directory path")
    parser.add_argument("--n-trials", type=int, default=50, help="Optuna trials per model")
    parser.add_argument("--train-start", default="2023-01-01", help="Training start date")
    parser.add_argument("--train-end", default="2025-08-15", help="Training end date")
    
    args = parser.parse_args()
    
    success = run_full_retrain(
        data_dir=args.data_dir,
        n_trials=args.n_trials,
        train_start=args.train_start,
        train_end=args.train_end
    )
    
    sys.exit(0 if success else 1)
