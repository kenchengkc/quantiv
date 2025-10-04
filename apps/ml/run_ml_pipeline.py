#!/usr/bin/env python3
"""
Complete ML Pipeline Runner
Orchestrates the full ML MVP2 pipeline: bias curves -> feature engineering -> training -> serving
"""
import os
import sys
from pathlib import Path
import logging
from datetime import datetime

# Add current directory to path for imports
sys.path.append(str(Path(__file__).parent))

from bias_curve_builder import BiaseCurveBuilder
from feature_engineering import FeatureEngineer
from model_trainer import ModelTrainer
from serving_pipeline import MLServingPipeline

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_complete_pipeline(data_dir: str = "../../data", 
                         models_dir: str = "models",
                         optimize_models: bool = True):
    """Run the complete ML pipeline"""
    
    logger.info("=== Starting Complete ML Pipeline ===")
    start_time = datetime.now()
    
    try:
        # Step 1: Build Historical Bias Curves
        logger.info("Step 1: Building historical bias curves...")
        bias_builder = BiaseCurveBuilder(data_dir)
        # Use recent data for faster initial training (2023-2024)
        bias_points = bias_builder.extract_historical_bias_points(
            start_date="2023-01-01",
            end_date="2024-06-30"
        )
        
        if not bias_points:
            logger.error("No bias points extracted - pipeline cannot continue")
            return False
        
        bias_curves = bias_builder.build_bias_curves(bias_points)
        bias_curves_path = bias_builder.save_bias_curves(bias_curves)
        logger.info(f"✅ Bias curves saved: {len(bias_curves)} entities")
        
        # Step 2: Feature Engineering
        logger.info("Step 2: Feature engineering for all horizons...")
        engineer = FeatureEngineer(data_dir)
        # Use same date range as bias curves for consistency
        feature_sets = engineer.extract_training_data(
            start_date="2023-01-01",
            end_date="2024-06-30"
        )
        
        if not feature_sets:
            logger.error("No training data extracted - pipeline cannot continue")
            return False
        
        training_paths = engineer.save_training_data(feature_sets)
        logger.info(f"✅ Training data saved for {len(feature_sets)} horizons")
        
        # Step 3: Model Training
        logger.info("Step 3: Training LightGBM models...")
        trainer = ModelTrainer(
            data_dir=str(Path(data_dir) / "ml_training"),
            models_dir=models_dir
        )
        
        results = trainer.train_all_models(optimize=optimize_models)
        
        if not results:
            logger.error("No models trained successfully - pipeline cannot continue")
            return False
        
        model_paths = trainer.save_models(results)
        predictions_path = trainer.generate_predictions(results)
        logger.info(f"✅ Models trained and saved: {len(results)} horizons")
        
        # Step 4: Test Serving Pipeline
        logger.info("Step 4: Testing serving pipeline...")
        serving_pipeline = MLServingPipeline(data_dir, models_dir)
        
        # Test with sample symbols
        test_forecasts = serving_pipeline.batch_forecast([
            ('AAPL', '2025-01-30', 'Technology'),
            ('MSFT', '2025-01-29', 'Technology'),
        ])
        
        logger.info(f"✅ Serving pipeline tested with {len(test_forecasts)} forecasts")
        
        # Pipeline Summary
        end_time = datetime.now()
        duration = end_time - start_time
        
        print("\n" + "="*60)
        print("ML PIPELINE COMPLETED SUCCESSFULLY")
        print("="*60)
        print(f"Duration: {duration}")
        print(f"Bias Curves: {len(bias_curves)} entities")
        print(f"Training Data: {len(feature_sets)} horizons")
        print(f"Trained Models: {len(results)} horizons")
        print(f"Test Forecasts: {len(test_forecasts)} symbols")
        
        print("\nModel Performance Summary:")
        for horizon, model_results in results.items():
            metrics = model_results['metrics']
            print(f"  T-{horizon}: Val MAE = {metrics['val_mae']:.4f}")
        
        print(f"\nBias Curves Path: {bias_curves_path}")
        print(f"Models Directory: {models_dir}")
        print(f"Training Data: {Path(data_dir) / 'ml_training'}")
        
        return True
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        return False

def main():
    """Main entry point"""
    
    # Configuration
    data_dir = "../../data"  # Relative to apps/ml/
    models_dir = "models"
    optimize_models = True  # Set to False for faster development
    
    # Ensure directories exist
    Path(models_dir).mkdir(parents=True, exist_ok=True)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    
    # Run pipeline
    success = run_complete_pipeline(
        data_dir=data_dir,
        models_dir=models_dir,
        optimize_models=optimize_models
    )
    
    if success:
        print("\n🎉 ML MVP2 Pipeline completed successfully!")
        print("Ready for production deployment with Math + ML expected moves")
    else:
        print("\n❌ Pipeline failed - check logs for details")
        sys.exit(1)

if __name__ == "__main__":
    main()
