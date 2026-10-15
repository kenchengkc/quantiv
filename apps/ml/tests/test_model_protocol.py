import json

import pytest

from ml.model_protocol import (
    CAUSAL_FEATURE_PROTOCOL,
    LEGACY_FEATURE_PROTOCOL,
    SESSION_TARGET_PROTOCOL,
    bundle_feature_protocol,
    feature_protocol,
    target_protocol,
)


def test_legacy_is_explicit_and_unknown_protocol_fails_closed():
    assert feature_protocol({}) == LEGACY_FEATURE_PROTOCOL
    assert feature_protocol({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}) == CAUSAL_FEATURE_PROTOCOL
    assert target_protocol({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}) == SESSION_TARGET_PROTOCOL
    with pytest.raises(ValueError, match="unsupported feature protocol"):
        feature_protocol({"feature_protocol": "unknown"})
    with pytest.raises(ValueError, match="incompatible target protocol"):
        target_protocol({"feature_protocol": CAUSAL_FEATURE_PROTOCOL, "target_protocol": "old"})


def test_bundle_rejects_mixed_feature_semantics(tmp_path):
    for horizon, protocol in ((1, LEGACY_FEATURE_PROTOCOL), (2, CAUSAL_FEATURE_PROTOCOL)):
        (tmp_path / f"metadata_T{horizon}.json").write_text(json.dumps({"feature_protocol": protocol}))
    with pytest.raises(ValueError, match="mixed feature protocols"):
        bundle_feature_protocol(tmp_path, horizons=(1, 2))


def test_bundle_reads_each_horizon_and_requires_metadata(tmp_path):
    (tmp_path / "metadata_T1.json").write_text(json.dumps({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}))
    assert bundle_feature_protocol(tmp_path, horizons=(1,)) == CAUSAL_FEATURE_PROTOCOL
    with pytest.raises(FileNotFoundError):
        bundle_feature_protocol(tmp_path, horizons=(1, 2))


def test_bundle_protocol_defaults_to_verified_manifest_heads(tmp_path, monkeypatch):
    from test_model_bundle import _keys
    from ml.evidence_receipt import build_evidence_receipt
    from ml.model_bundle import create_signed_bundle, required_artifact_names
    private, public = _keys()
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", public.decode())
    models = tmp_path / "models"
    models.mkdir()
    for name in required_artifact_names((4, 25)):
        (models / name).write_text(json.dumps({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}) if name.startswith("metadata") else "native")
    report = {"status": "passed", "stages": {"models": {"horizons": [4, 25]}}}
    receipt = build_evidence_receipt(report, scope="models", repo_root=tmp_path,
        data_dir=tmp_path, training_dir=tmp_path / "training", models_dir=models,
        forecast_path=None, horizons=(4, 25))
    receipt_path, report_path = tmp_path / "receipt.json", tmp_path / "report.json"
    receipt_path.write_text(json.dumps(receipt))
    report_path.write_text(json.dumps(report))
    bundle, _ = create_signed_bundle(models, tmp_path / "bundles", receipt_path=receipt_path,
        validation_report_path=report_path, source_revision="test", horizons=(4, 25), private_key=private)
    assert bundle_feature_protocol(bundle) == CAUSAL_FEATURE_PROTOCOL
    (bundle / "metadata_T7.json").write_text(json.dumps({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}))
    with pytest.raises(ValueError, match="horizon"):
        bundle_feature_protocol(bundle, horizons=(4, 7))
