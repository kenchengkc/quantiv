"""Quantiv ML serving package.

Importable modules live here so consumers (apps/backend) can use the
clean ``from ml.serving_pipeline import MLServingPipeline`` form after
``pip install -e apps/ml``. Training scripts that haven't been migrated
into the package layout (``feature_engineering.py``,
``model_trainer.py``) stay at ``apps/ml/`` as standalone
entry points invoked by their full path.
"""

__all__ = ["pipeline_validation", "quantiles", "serving_pipeline", "training_split"]
