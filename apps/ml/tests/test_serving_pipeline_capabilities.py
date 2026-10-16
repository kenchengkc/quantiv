from datetime import date, timedelta

import numpy as np
import pandas as pd
import duckdb
import pytest

from ml.serving_pipeline import MLServingPipeline


class _Estimator:
    def predict(self, frame):
        return np.full(len(frame), .5)


def _pipeline(tmp_path, manifest, metadata=None):
    pipeline = MLServingPipeline.__new__(MLServingPipeline)
    pipeline.models_dir = tmp_path
    pipeline._manifest = manifest
    pipeline.models = {7: {"model": _Estimator(), "feature_names": ["feature"],
                           "metadata": metadata or {}}}
    pipeline.extract_ml_features = lambda *args: pd.DataFrame({"feature": [1.]})
    pipeline.calculate_math_baseline = lambda *args: {"em_math": .05}
    return pipeline


def test_signed_pipeline_never_substitutes_nearest_head_or_baseline(tmp_path):
    pipeline = _pipeline(tmp_path, {"horizons": [7]})
    result = pipeline.generate_forecast("TEST", (date.today() + timedelta(days=6)).isoformat())
    assert "exact forecast horizon" in result["error"]
    assert "em_ml" not in result


def test_signed_pipeline_cannot_use_legacy_features_for_a_causal_head(tmp_path):
    metadata = {"feature_protocol": "quantiv.earnings-causal.v2",
                "supported_cohorts": ["strict_options", "optionless"]}
    pipeline = _pipeline(tmp_path, {"horizons": [7]}, metadata)
    result = pipeline.generate_forecast("TEST", (date.today() + timedelta(days=7)).isoformat())
    assert "canonical feature snapshot" in result["error"]
    assert "em_ml" not in result


def test_signed_pipeline_enforces_cohort_capability(tmp_path):
    manifest = {"horizons": [7], "serving_capabilities": {"7": ["optionless"]}}
    metadata = {"supported_cohorts": ["strict_options", "optionless"]}
    pipeline = _pipeline(tmp_path, manifest, metadata)
    result = pipeline.generate_forecast("TEST", (date.today() + timedelta(days=7)).isoformat())
    assert "not admitted" in result["error"]
    assert "em_ml" not in result


def test_unsigned_legacy_pipeline_retains_nearest_head_behavior(tmp_path):
    pipeline = _pipeline(tmp_path, None)
    assert pipeline.predict_ml_correction("TEST", 6)["model_horizon"] == 7


def test_public_legacy_pipeline_constructor_is_explicitly_retired(tmp_path, monkeypatch):
    for method in ("_setup_duckdb", "_load_models", "_load_bias_curves"):
        monkeypatch.setattr(MLServingPipeline, method, lambda self: None)
    with pytest.raises(RuntimeError, match="retired"):
        MLServingPipeline(data_dir=str(tmp_path), models_dir=str(tmp_path))


@pytest.mark.parametrize("manifest", [None, {"horizons": [7]}])
def test_legacy_pipeline_never_publishes_an_issuer_without_source_coverage(tmp_path, manifest):
    pipeline = _pipeline(tmp_path, manifest)
    with duckdb.connect() as connection:
        pipeline.conn = connection
        connection.execute("CREATE TABLE options_paired AS SELECT 'TEST' act_symbol, CURRENT_DATE quote_date")
        connection.execute("""CREATE TABLE v_corporate_action_coverage AS SELECT 'OTHER' act_symbol,
            DATE '2020-01-01' window_start, DATE '2030-01-01' window_end""")
        result = pipeline.generate_forecast("TEST", (date.today() + timedelta(days=7)).isoformat())
        assert "error" in result
        assert "em_ml" not in result
