"""Quantiv ML serving package.

Importable modules live here so consumers (apps/backend) can use the
clean ``from ml.serving_pipeline import MLServingPipeline`` form after
``pip install -e apps/ml``. Training scripts that haven't been migrated
into the package layout (``feature_engineering_v3.py``,
``model_trainer_v3.py``, ``bias_curve_builder.py``) stay at ``apps/ml/`` as standalone
entry points invoked by their full path.
"""

__all__ = ["quantiles", "serving_pipeline", "training_split"]
