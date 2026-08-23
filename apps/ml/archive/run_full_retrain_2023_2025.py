#!/usr/bin/env python3
"""
Archived legacy retrain entrypoint.

Full Retrain: Train models on ALL available data (2023-2025)
This maximizes model learning for production deployment

The active training path is feature_engineering.py + model_trainer.py.
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime

ARCHIVE_DIR = Path(__file__).resolve().parent
ML_ROOT = ARCHIVE_DIR.parent
REPO_ROOT = ML_ROOT.parent.parent  # apps/ml → apps → repo root
DEFAULT_DATA_DIR = str(REPO_ROOT / "data")
sys.path.insert(0, str(ARCHIVE_DIR))
sys.path.insert(0, str(ML_ROOT))

from bias_curve_builder import BiasCurveBuilder
from ml.serving_pipeline import MLServingPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

def run_full_retrain(
    data_dir: str = DEFAULT_DATA_DIR,
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
        builder = BiasCurveBuilder(data_dir)
        
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
        
        import subprocess

        logger.info(
            "\nStep 2: Feature engineering via apps/ml/feature_engineering.py "
            f"({train_start} to {train_end})..."
        )
        fe = subprocess.run(
            [
                sys.executable,
                str(ML_ROOT / "feature_engineering.py"),
                "--start-date",
                train_start,
                "--end-date",
                train_end,
            ],
            check=False,
        )
        if fe.returncode != 0:
            logger.error("Feature engineering failed")
            return False

        logger.info("\nStep 3: Training via apps/ml/model_trainer.py --tune")
        tr = subprocess.run(
            [sys.executable, str(ML_ROOT / "model_trainer.py"), "--tune"],
            check=False,
        )
        if tr.returncode != 0:
            logger.error("Model training failed")
            return False

        logger.info("\nStep 4: Validating serving pipeline...")
        serving = MLServingPipeline(data_dir)
        logger.info("Serving pipeline loaded:")
        logger.info(f"  - {len(serving.models)} models")
        logger.info(f"  - {len(serving.bias_curves)} bias curve entities")

        duration = (datetime.now() - start_time).total_seconds()
        logger.info("\n" + "=" * 80)
        logger.info(f"FULL RETRAIN COMPLETED in {duration:.1f}s")
        logger.info("=" * 80)
        logger.info(f"  Period: {train_start} to {train_end}")
        logger.info(f"  Optimization trials requested: {n_trials}")
        return True

    except Exception as e:
        logger.error(f"Pipeline failed with error: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Full Retrain on 2023-2025 Data")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Data directory path")
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
