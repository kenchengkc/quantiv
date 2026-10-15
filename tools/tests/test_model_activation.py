from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ml.evidence_receipt import build_evidence_receipt
from ml.model_bundle import ModelBundleError, create_signed_bundle, required_artifact_names
from scripts.activate_model_bundle import activate


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def _signed_decision(tmp_path, monkeypatch, horizons=(1, 2, 3, 7, 14, 21)):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode())
    models = tmp_path / "models"
    models.mkdir()
    for name in required_artifact_names(horizons):
        (models / name).write_text("native")
    report = {"status": "passed", "stages": {"models": {"horizons": list(horizons)}}}
    receipt = build_evidence_receipt(
        report, scope="models", repo_root=tmp_path, data_dir=tmp_path,
        training_dir=tmp_path / "training", models_dir=models,
        forecast_path=None, horizons=horizons,
    )
    report_path, receipt_path = tmp_path / "report.json", tmp_path / "receipt.json"
    report_path.write_text(json.dumps(report))
    receipt_path.write_text(json.dumps(receipt))
    bundle_root = tmp_path / "bundles"
    _, manifest = create_signed_bundle(
        models, bundle_root, receipt_path=receipt_path,
        validation_report_path=report_path, source_revision="test",
        horizons=horizons, private_key=private,
    )
    bundle_id = manifest["bundle_id"]
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({
        "schema": "quantiv.model-decision.v1", "status": "passed",
        "champion_bundle_id": bundle_id,
    }))
    return decision, bundle_id, bundle_root


def _response(bundle_id: str, horizons=(1, 2, 3, 7, 14, 21)) -> dict:
    return {
        "status": "activated",
        "bundle_id": bundle_id,
        "models_dir": f"/data/models/versions/{bundle_id}",
        "preflight": [{
            "horizon_days": horizon, "feature_count": 20,
            "quantile_model_count": 5, "feature_schema_hash": "b" * 64,
        } for horizon in horizons],
    }


@pytest.mark.parametrize("horizons", [(1, 2, 3, 7, 14, 21), tuple(range(1, 26))])
def test_activation_receipt_proves_exact_bundle_and_preflight(tmp_path, monkeypatch, horizons):
    decision, bundle_id, bundle_root = _signed_decision(tmp_path, monkeypatch, horizons)
    receipt_path = tmp_path / "activation.json"
    receipt = activate(
        backend_url="https://api.example.test", admin_api_key="secret",
        decision_path=decision, receipt_path=receipt_path, bundle_root=bundle_root,
        opener=lambda *_args, **_kwargs: _Response(_response(bundle_id, horizons)),
    )
    assert receipt["status"] == "passed"
    assert receipt["expected_bundle_id"] == bundle_id
    assert receipt["activated_bundle_id"] == bundle_id
    assert receipt["receipt_id"].startswith("sha256:")
    assert [row["horizon_days"] for row in receipt["preflight"]] == list(horizons)
    assert json.loads(receipt_path.read_text()) == receipt


def test_activation_rejects_different_backend_bundle(tmp_path, monkeypatch):
    decision, _, bundle_root = _signed_decision(tmp_path, monkeypatch)
    receipt_path = tmp_path / "activation.json"
    with pytest.raises(ValueError, match="different bundle"):
        activate(
            backend_url="https://api.example.test", admin_api_key="secret",
            decision_path=decision, receipt_path=receipt_path, bundle_root=bundle_root,
            opener=lambda *_args, **_kwargs: _Response(_response("c" * 64)),
        )
    assert not receipt_path.exists()


@pytest.mark.parametrize("horizons", [(1, 2, 3, 7, 14, 21), (4, 25)])
def test_activation_rejects_incomplete_native_preflight(tmp_path, monkeypatch, horizons):
    decision, bundle_id, bundle_root = _signed_decision(tmp_path, monkeypatch, horizons)
    response = _response(bundle_id, horizons)
    response["preflight"].pop()
    with pytest.raises(ValueError, match="preflight horizons"):
        activate(
            backend_url="https://api.example.test", admin_api_key="secret",
            decision_path=decision, receipt_path=tmp_path / "activation.json",
            bundle_root=bundle_root,
            opener=lambda *_args, **_kwargs: _Response(response),
        )


def test_activation_rejects_altered_local_bundle_before_sending(tmp_path, monkeypatch):
    decision, bundle_id, bundle_root = _signed_decision(tmp_path, monkeypatch, (4, 25))
    (bundle_root / bundle_id / "lgbm_T25.txt").write_text("changed")
    with pytest.raises(ModelBundleError, match="mismatch"):
        activate(
            backend_url="https://api.example.test", admin_api_key="secret",
            decision_path=decision, receipt_path=tmp_path / "activation.json",
            bundle_root=bundle_root,
            opener=lambda *_args, **_kwargs: pytest.fail("request sent before local verification"),
        )
