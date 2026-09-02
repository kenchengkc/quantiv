from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _decision(path: Path, bundle_id: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "quantiv.model-decision.v1",
                "status": "passed",
                "champion_bundle_id": bundle_id,
            }
        )
    )
    return path


def _response(bundle_id: str) -> dict:
    return {
        "status": "activated",
        "bundle_id": bundle_id,
        "models_dir": f"/data/models/versions/{bundle_id}",
        "preflight": [
            {
                "horizon_days": horizon,
                "feature_count": 20,
                "quantile_model_count": 5,
                "feature_schema_hash": "b" * 64,
            }
            for horizon in (1, 2, 3, 7, 14, 21)
        ],
    }


def test_activation_receipt_proves_exact_bundle_and_preflight(tmp_path: Path) -> None:
    bundle_id = "a" * 64
    decision = _decision(tmp_path / "decision.json", bundle_id)
    receipt_path = tmp_path / "activation.json"

    receipt = activate(
        backend_url="https://api.example.test",
        admin_api_key="secret",
        decision_path=decision,
        receipt_path=receipt_path,
        opener=lambda *_args, **_kwargs: _Response(_response(bundle_id)),
    )

    assert receipt["status"] == "passed"
    assert receipt["expected_bundle_id"] == bundle_id
    assert receipt["activated_bundle_id"] == bundle_id
    assert receipt["receipt_id"].startswith("sha256:")
    assert json.loads(receipt_path.read_text()) == receipt


def test_activation_rejects_different_backend_bundle(tmp_path: Path) -> None:
    expected = "a" * 64
    decision = _decision(tmp_path / "decision.json", expected)
    receipt_path = tmp_path / "activation.json"

    with pytest.raises(ValueError, match="different bundle"):
        activate(
            backend_url="https://api.example.test",
            admin_api_key="secret",
            decision_path=decision,
            receipt_path=receipt_path,
            opener=lambda *_args, **_kwargs: _Response(_response("c" * 64)),
        )

    assert not receipt_path.exists()


def test_activation_rejects_incomplete_native_preflight(tmp_path: Path) -> None:
    bundle_id = "a" * 64
    decision = _decision(tmp_path / "decision.json", bundle_id)
    response = _response(bundle_id)
    response["preflight"] = response["preflight"][:-1]

    with pytest.raises(ValueError, match="preflight horizons"):
        activate(
            backend_url="https://api.example.test",
            admin_api_key="secret",
            decision_path=decision,
            receipt_path=tmp_path / "activation.json",
            opener=lambda *_args, **_kwargs: _Response(response),
        )
