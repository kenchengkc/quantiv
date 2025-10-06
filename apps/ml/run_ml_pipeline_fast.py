"""
Fast ML Pipeline Runner - Optimized for MVP2 validation

Uses smaller date range and cached bias curves to validate end-to-end pipeline quickly.
"""

import os
import sys
import logging
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.bias_curve_builder import BiaseCurveBuilder
from ml.feature_engineering import FeatureEngineer
from ml.model_trainer import ModelTrainer
from ml.serving_pipeline import MLServingPipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)


def run_fast_ml_pipeline(data_dir: str = "../../data",
                         use_cached_bias_curves: bool = True,
                         optimize_models: bool = False):
    """Run optimized ML pipeline for faster validation"""
    
    logger.info("=== Starting Fast ML Pipeline (MVP2 Validation) ===")
    start_time = datetime.now()
    
    try:
        # Step 1: Build or Load Bias Curves
        logger.info("Step 1: Loading/building bias curves...")
        bias_builder = BiaseCurveBuilder(data_dir)
        
        bias_curves_path = os.path.join(data_dir, "bias_curves.parquet")
        if use_cached_bias_curves and os.path.exists(bias_curves_path):
            logger.info(f"✅ Using cached bias curves from {bias_curves_path}")
            # Bias curves already exist, skip extraction
        else:
            # Extract on smaller date range for speed (Q1 2024)
            logger.info("Extracting bias curves from Q1 2024...")
            bias_points = bias_builder.extract_historical_bias_points(
                start_date="2024-01-01",
                end_date="2024-03-31"
            )
            
            if not bias_points:
                logger.error("No bias points extracted - pipeline cannot continue")
                return False
            
            bias_curves = bias_builder.build_bias_curves(bias_points)
            bias_curves_path = bias_builder.save_bias_curves(bias_curves)
            logger.info(f"✅ Bias curves saved: {len(bias_curves)} entities")
        
        # Step 2: Feature Engineering (Q1 2024 only for speed)
        logger.info("Step 2: Feature engineering for all horizons (Q1 2024)...")
        engineer = FeatureEngineer(data_dir)
        feature_sets = engineer.extract_training_data(
            start_date="2024-01-01",
            end_date="2024-03-31"
        )
        
        if not feature_sets:
            logger.error("No training data extracted - pipeline cannot continue")
            return False
        
        training_paths = engineer.save_training_data(feature_sets)
        logger.info(f"✅ Training data saved for {len(feature_sets)} horizons")
        
        # Step 3: Model Training (fast mode - no optimization)
        logger.info("Step 3: Training models per horizon...")
        logger.info(f"Optimization: {'enabled' if optimize_models else 'disabled (fast mode)'}")
        # ModelTrainer needs the ml_training subdirectory
        training_dir = os.path.join(data_dir, "ml_training")
        trainer = ModelTrainer(training_dir)
        
        # Train all models at once
        model_results = trainer.train_all_models(optimize=optimize_models)
        
        # Log results
        for horizon, result in sorted(model_results.items()):
            metrics = result['metrics']
            logger.info(f"✅ T-{horizon}: MAE={metrics['val_mae']:.4f}, RMSE={metrics['val_rmse']:.4f}")
        
        # Save models
        saved_paths = trainer.save_models(model_results)
        logger.info(f"\n✅ Trained and saved {len(model_results)} models")
        
        # Step 4: Test Serving Pipeline
        logger.info("\nStep 4: Testing serving pipeline...")
        serving = MLServingPipeline(data_dir)
        
        # Test with sample symbols
        test_cases = [
            ('AAPL', '2024-04-01', 'Technology'),
            ('MSFT', '2024-04-15', 'Technology'),
            ('TSLA', '2024-04-20', None),
        ]
        
        logger.info("\nSample Forecasts:")
        logger.info("-" * 80)
        for symbol, date, sector in test_cases:
            try:
                forecast = serving.generate_forecast(symbol, date, sector)
                logger.info(f"{symbol:6s} | Math: {forecast['em_math']:.3f} | "
                           f"ML: {forecast['em_ml']:.3f} | "
                           f"Bands: [{forecast['p10']:.3f}, {forecast['p90']:.3f}] | "
                           f"Conf: {forecast['combined_confidence']:.2f}")
            except Exception as e:
                logger.warning(f"{symbol:6s} | Failed: {e}")
        
        logger.info("-" * 80)
        
        # Summary
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"\n{'='*80}")
        logger.info(f"✅ Pipeline completed successfully in {duration:.1f}s")
        logger.info(f"{'='*80}")
        logger.info("\nModel Results:")
        for horizon, result in sorted(model_results.items()):
            metrics = result.get('metrics', {})
            logger.info(f"  T-{horizon}: Val MAE={metrics.get('val_mae', 0):.4f}, "
                       f"Val RMSE={metrics.get('val_rmse', 0):.4f}")
        
        logger.info(f"\nNext Steps:")
        logger.info(f"  1. Expand to full historical data (2023-2024) for better models")
        logger.info(f"  2. Enable hyperparameter optimization (optimize_models=True)")
        logger.info(f"  3. Validate on held-out test set (2024-Q2)")
        logger.info(f"  4. Integrate into backend API for live serving")
        logger.info(f"  5. Update frontend to display Math vs ML forecasts")
        
        return True
        
    except Exception as e:
        logger.error(f"Pipeline failed with error: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Run fast ML pipeline for MVP2 validation')
    parser.add_argument('--data-dir', default='../../data', help='Data directory')
    parser.add_argument('--no-cache', action='store_true', help='Rebuild bias curves')
    parser.add_argument('--optimize', action='store_true', help='Enable hyperparameter optimization (slower)')
    
    args = parser.parse_args()
    
    success = run_fast_ml_pipeline(
        data_dir=args.data_dir,
        use_cached_bias_curves=not args.no_cache,
        optimize_models=args.optimize
    )
    
    sys.exit(0 if success else 1)
