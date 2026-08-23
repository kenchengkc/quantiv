from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ml.model_bundle import (
    ModelBundleError,
    create_signed_bundle,
    create_signed_control_pointer,
    create_signed_monitor_receipt,
    create_signed_registry,
    required_artifact_names,
    verify_bundle_dir,
    verify_control_pointer,
    verify_monitor_receipt,
    verify_registry,
)


def _keys() -> tuple[bytes, bytes]:
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
    return private_pem, public_pem


def _validated_inputs(root: Path) -> tuple[Path, Path, Path]:
    models = root / "models"
    models.mkdir()
    for name in required_artifact_names([1]):
        (models / name).write_bytes(f"artifact:{name}".encode())
    report = root / "report.json"
    report.write_text(json.dumps({"status": "passed"}))
    receipt = root / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "receipt_id": "sha256:" + "a" * 64,
                "quality": {"status": "passed"},
            }
        )
    )
    return models, report, receipt


def test_signed_bundle_detects_any_artifact_change(tmp_path: Path) -> None:
    private, public = _keys()
    models, report, receipt = _validated_inputs(tmp_path)
    bundle_dir, manifest = create_signed_bundle(
        models,
        tmp_path / "bundles",
        receipt_path=receipt,
        validation_report_path=report,
        source_revision="abc123",
        horizons=[1],
        private_key=private,
    )

    verified = verify_bundle_dir(bundle_dir, public_key=public)
    assert verified["bundle_id"] == manifest["bundle_id"]

    (bundle_dir / "lgbm_T1.txt").write_bytes(b"altered")
    with pytest.raises(ModelBundleError, match="mismatch"):
        verify_bundle_dir(bundle_dir, public_key=public)


def test_control_pointer_is_signed_and_tamper_evident() -> None:
    private, public = _keys()
    pointer = create_signed_control_pointer(
        bundle_id="b" * 64,
        previous_bundle_id="a" * 64,
        decision={"action": "promote"},
        private_key=private,
    )
    assert verify_control_pointer(pointer, public_key=public)["champion_bundle_id"] == "b" * 64

    pointer["champion_bundle_id"] = "c" * 64
    with pytest.raises(ModelBundleError, match="signature"):
        verify_control_pointer(pointer, public_key=public)


def test_failed_validation_cannot_be_packaged(tmp_path: Path) -> None:
    private, _ = _keys()
    models, report, receipt = _validated_inputs(tmp_path)
    report.write_text(json.dumps({"status": "failed"}))
    with pytest.raises(ModelBundleError, match="did not pass"):
        create_signed_bundle(
            models,
            tmp_path / "bundles",
            receipt_path=receipt,
            validation_report_path=report,
            source_revision="abc123",
            horizons=[1],
            private_key=private,
        )


def test_registry_and_monitoring_ledger_are_signed(tmp_path: Path) -> None:
    private, public = _keys()
    registry = create_signed_registry(
        champion_bundle_id="a" * 64,
        challenger_bundle_id="b" * 64,
        previous_bundle_id=None,
        decision={"action": "retain_champion"},
        private_key=private,
    )
    assert verify_registry(registry, public_key=public)["challenger_bundle_id"] == "b" * 64

    ledger = tmp_path / "ledger.parquet"
    report = tmp_path / "report.json"
    ledger.write_bytes(b"ledger")
    report.write_bytes(b"report")
    receipt = create_signed_monitor_receipt(
        ledger_path=ledger,
        report_path=report,
        snapshot_date="2026-08-23",
        private_key=private,
    )
    verify_monitor_receipt(
        receipt,
        ledger_path=ledger,
        report_path=report,
        public_key=public,
    )
    ledger.write_bytes(b"tampered")
    with pytest.raises(ModelBundleError, match="ledger"):
        verify_monitor_receipt(
            receipt,
            ledger_path=ledger,
            report_path=report,
            public_key=public,
        )
