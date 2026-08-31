from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from model_control_plane import evaluate_outcomes  # noqa: E402
from ml.model_bundle import verify_outcome_receipt  # noqa: E402


def test_insufficient_outcomes_are_retained_and_signed(tmp_path, monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private_pem.decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", public_pem.decode())
    models_root = tmp_path / "models"
    report_path = tmp_path / "validation" / "model_outcomes.json"
    args = argparse.Namespace(
        models_root=models_root,
        training_dir=tmp_path / "training",
        min_common_rows=30,
        report=report_path,
        monitoring_report=None,
        history=None,
        history_limit=52,
    )

    assert evaluate_outcomes(args) == 0

    monitoring = models_root / "monitoring"
    latest = monitoring / "latest_outcomes.json"
    history = monitoring / "outcome_history.json"
    receipt_path = monitoring / "latest_outcomes.receipt.json"
    payload = json.loads(latest.read_text())
    retained = json.loads(history.read_text())
    receipt = json.loads(receipt_path.read_text())
    assert payload["status"] == "insufficient_data"
    assert retained["evaluations"][0]["status"] == "insufficient_data"
    assert report_path.read_bytes() == latest.read_bytes()
    verify_outcome_receipt(
        receipt,
        report_path=latest,
        history_path=history,
        public_key=public_pem,
    )
