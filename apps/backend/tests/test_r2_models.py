from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from ml.model_bundle import (  # noqa: E402
    create_signed_bundle,
    create_signed_control_pointer,
    required_artifact_names,
)
from services import r2_models  # noqa: E402


class _FakeR2:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803 - boto API shape
        _ = Bucket
        return {"Body": io.BytesIO(self.objects[Key])}

    def download_file(self, bucket: str, key: str, destination: str) -> None:
        _ = bucket
        Path(destination).write_bytes(self.objects[key])


def _keys(tmp_path: Path) -> tuple[bytes, Path]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_pem, public_path


def _remote_objects(
    tmp_path: Path,
    *,
    private: bytes | None = None,
    public_path: Path | None = None,
    marker: str = "first",
) -> tuple[dict[str, bytes], str, Path, bytes]:
    if private is None or public_path is None:
        private, public_path = _keys(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    for name in required_artifact_names([1]):
        (models / name).write_bytes(f"artifact:{marker}:{name}".encode())
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"status": "passed"}))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "receipt_id": "sha256:" + "a" * 64,
                "quality": {"status": "passed"},
            }
        )
    )
    bundle_dir, manifest = create_signed_bundle(
        models,
        tmp_path / "bundles",
        receipt_path=receipt,
        validation_report_path=report,
        source_revision=marker,
        horizons=[1],
        private_key=private,
    )
    bundle_id = manifest["bundle_id"]
    pointer = create_signed_control_pointer(
        bundle_id=bundle_id,
        previous_bundle_id=None,
        decision={"action": "bootstrap"},
        private_key=private,
    )
    objects = {
        r2_models.CONTROL_KEY: json.dumps(pointer).encode(),
        f"models/bundles/{bundle_id}/manifest.json": json.dumps(manifest).encode(),
    }
    for path in bundle_dir.iterdir():
        if path.name != "manifest.json":
            objects[f"models/bundles/{bundle_id}/{path.name}"] = path.read_bytes()
    return objects, bundle_id, public_path, private


def test_sync_activates_only_a_complete_verified_bundle(tmp_path: Path, monkeypatch) -> None:
    objects, bundle_id, public_path, _ = _remote_objects(tmp_path)
    monkeypatch.setenv("R2_BUCKET", "test")
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public_path))
    monkeypatch.setattr(r2_models, "_build_client", lambda: _FakeR2(objects))

    target = tmp_path / "volume"
    result = r2_models.sync_models_from_r2(target)

    assert result.activated is True
    assert result.bundle_id == bundle_id
    assert result.downloaded_files == 7
    assert (target / "current").resolve() == target / "versions" / bundle_id
    assert r2_models.active_models_dir(target) == target / "versions" / bundle_id


def test_corrupt_download_never_replaces_active_bundle(tmp_path: Path, monkeypatch) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    objects, bundle_id, public_path, private = _remote_objects(first_root)
    monkeypatch.setenv("R2_BUCKET", "test")
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public_path))
    target = tmp_path / "volume"
    monkeypatch.setattr(r2_models, "_build_client", lambda: _FakeR2(objects))
    assert r2_models.sync_models_from_r2(target).activated
    current_before = (target / "current").resolve()

    corrupted, next_bundle_id, _, _ = _remote_objects(
        second_root,
        private=private,
        public_path=public_path,
        marker="second",
    )
    corrupted[f"models/bundles/{next_bundle_id}/lgbm_T1.txt"] = b"tampered"
    monkeypatch.setattr(r2_models, "_build_client", lambda: _FakeR2(corrupted))
    result = r2_models.sync_models_from_r2(target)

    assert result.activated is False
    assert (target / "current").resolve() == current_before
    assert current_before.name == bundle_id


def test_expected_bundle_mismatch_never_activates(tmp_path: Path, monkeypatch) -> None:
    objects, _bundle_id, public_path, _ = _remote_objects(tmp_path)
    monkeypatch.setenv("R2_BUCKET", "test")
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public_path))
    monkeypatch.setattr(r2_models, "_build_client", lambda: _FakeR2(objects))
    target = tmp_path / "volume"

    result = r2_models.sync_models_from_r2(
        target,
        expected_bundle_id="f" * 64,
    )

    assert result.activated is False
    assert result.error == "R2 champion does not match the expected promotion decision"
    assert not (target / "current").exists()


def test_native_preflight_failure_never_activates(tmp_path: Path, monkeypatch) -> None:
    objects, bundle_id, public_path, _ = _remote_objects(tmp_path)
    monkeypatch.setenv("R2_BUCKET", "test")
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public_path))
    monkeypatch.setattr(r2_models, "_build_client", lambda: _FakeR2(objects))
    target = tmp_path / "volume"

    def reject(_models_dir: Path) -> None:
        raise ValueError("native preflight failed")

    result = r2_models.sync_models_from_r2(
        target,
        expected_bundle_id=bundle_id,
        pre_activate=reject,
    )

    assert result.activated is False
    assert result.error == "native preflight failed"
    assert not (target / "current").exists()
