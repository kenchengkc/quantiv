"""ML serving package for the backend.

After ``pip install -e apps/ml``: ``from ml.serving_pipeline import MLServingPipeline``.
Train with ``apps/ml/feature_engineering.py`` and ``apps/ml/model_trainer.py``.
"""

__all__ = ["pipeline_validation", "quantiles", "serving_pipeline", "training_split"]
