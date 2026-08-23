"""Signed, content-addressed model bundles and control pointers.

The signing key exists only in the retraining workflow.  Serving processes ship
the public key and reject unsigned manifests, altered artifacts, and pointer
replays to unknown bundle content.  Bundle directories are immutable; only the
small signed control pointer changes during promotion or rollback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ml.model_artifact import point_model_name, quantile_model_name, sha256_file

BUNDLE_SCHEMA = "quantiv.model-bundle.v1"
CONTROL_SCHEMA = "quantiv.model-control.v1"
SIGNATURE_ALGORITHM = "ed25519"
DEFAULT_HORIZONS = (1, 2, 3, 7, 14, 21)
DEFAULT_QUANTILES = (10, 25, 50, 75, 90)
_BUNDLE_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ModelBundleError(RuntimeError):
    """A model bundle or control pointer failed a fail-closed trust check."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_key_bytes() -> bytes:
    configured = os.getenv("MODEL_BUNDLE_PUBLIC_KEY")
    if configured:
        candidate = Path(configured)
        return candidate.read_bytes() if candidate.is_file() else configured.encode()
    return files("ml").joinpath("model_bundle_public_key.pem").read_bytes()


def _private_key_bytes(value: str | bytes | None = None) -> bytes:
    material = value if value is not None else os.getenv("MODEL_BUNDLE_SIGNING_KEY")
    if not material:
        raise ModelBundleError("MODEL_BUNDLE_SIGNING_KEY is required to publish a bundle")
    return material if isinstance(material, bytes) else material.encode()


def _sign(payload: Mapping[str, Any], private_key: str | bytes | None = None) -> dict[str, Any]:
    key = serialization.load_pem_private_key(_private_key_bytes(private_key), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ModelBundleError("model bundle signing key must be Ed25519")
    signature = key.sign(_canonical(payload))
    return {
        **payload,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "value": base64.b64encode(signature).decode(),
        },
    }


def _public_from_private(private_key: str | bytes | None) -> bytes:
    key = serialization.load_pem_private_key(_private_key_bytes(private_key), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ModelBundleError("model bundle signing key must be Ed25519")
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def verify_signed_payload(
    envelope: Mapping[str, Any], *, public_key: str | bytes | None = None
) -> dict[str, Any]:
    payload = dict(envelope)
    signature = payload.pop("signature", None)
    if not isinstance(signature, Mapping):
        raise ModelBundleError("signed payload has no signature")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ModelBundleError("signed payload uses an unsupported algorithm")
    try:
        signature_bytes = base64.b64decode(str(signature["value"]), validate=True)
    except (KeyError, ValueError) as exc:
        raise ModelBundleError("signed payload has an invalid signature encoding") from exc
    material = public_key if public_key is not None else _public_key_bytes()
    material_bytes = material if isinstance(material, bytes) else material.encode()
    key = serialization.load_pem_public_key(material_bytes)
    if not isinstance(key, Ed25519PublicKey):
        raise ModelBundleError("model bundle public key must be Ed25519")
    try:
        key.verify(signature_bytes, _canonical(payload))
    except InvalidSignature as exc:
        raise ModelBundleError("signed payload signature is invalid") from exc
    return payload


def required_artifact_names(
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    quantiles: Sequence[int] = DEFAULT_QUANTILES,
) -> list[str]:
    names: list[str] = []
    for horizon in sorted(set(horizons)):
        names.append(point_model_name(horizon))
        names.extend(quantile_model_name(horizon, q) for q in quantiles)
        names.append(f"metadata_T{horizon}.json")
    return names


def _bundle_core(
    *,
    artifacts: list[dict[str, Any]],
    receipt_id: str,
    horizons: Sequence[int],
    source_revision: str,
    created_at: str,
) -> dict[str, Any]:
    identity = {
        "artifacts": artifacts,
        "receipt_id": receipt_id,
        "horizons": sorted(set(horizons)),
        "source_revision": source_revision,
    }
    bundle_id = hashlib.sha256(_canonical(identity)).hexdigest()
    return {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": bundle_id,
        "created_at": created_at,
        **identity,
    }


def create_signed_bundle(
    models_dir: Path,
    bundle_root: Path,
    *,
    receipt_path: Path,
    validation_report_path: Path,
    source_revision: str,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    private_key: str | bytes | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Copy a passed model set into an immutable, signed bundle directory."""
    report = json.loads(validation_report_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if report.get("status") != "passed" or receipt.get("quality", {}).get("status") != "passed":
        raise ModelBundleError("refusing to package a model set that did not pass validation")
    receipt_id = str(receipt.get("receipt_id", ""))
    if not receipt_id.startswith("sha256:"):
        raise ModelBundleError("model validation receipt has no content-addressed receipt_id")

    names = required_artifact_names(horizons)
    missing = [name for name in names if not (models_dir / name).is_file()]
    if missing:
        raise ModelBundleError(f"model bundle is incomplete: {missing}")
    artifacts = [
        {
            "name": name,
            "bytes": (models_dir / name).stat().st_size,
            "sha256": sha256_file(models_dir / name),
        }
        for name in names
    ]
    core = _bundle_core(
        artifacts=artifacts,
        receipt_id=receipt_id,
        horizons=horizons,
        source_revision=source_revision,
        created_at=_utc_now(),
    )
    signing_public_key = _public_from_private(private_key)
    manifest = _sign(core, private_key)
    bundle_id = core["bundle_id"]
    destination = bundle_root / bundle_id
    if destination.exists():
        verify_bundle_dir(destination, public_key=signing_public_key)
        return destination, manifest

    bundle_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{bundle_id[:12]}-", dir=bundle_root))
    try:
        for artifact in artifacts:
            shutil.copy2(models_dir / artifact["name"], temporary / artifact["name"])
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        verify_bundle_dir(temporary, public_key=signing_public_key)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, manifest


def verify_bundle_manifest(
    manifest: Mapping[str, Any], *, public_key: str | bytes | None = None
) -> dict[str, Any]:
    payload = verify_signed_payload(manifest, public_key=public_key)
    if payload.get("schema") != BUNDLE_SCHEMA:
        raise ModelBundleError("unsupported model bundle schema")
    bundle_id = str(payload.get("bundle_id", ""))
    if not _BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ModelBundleError("invalid model bundle id")
    expected = _bundle_core(
        artifacts=list(payload.get("artifacts") or []),
        receipt_id=str(payload.get("receipt_id", "")),
        horizons=list(payload.get("horizons") or []),
        source_revision=str(payload.get("source_revision", "")),
        created_at=str(payload.get("created_at", "")),
    )["bundle_id"]
    if bundle_id != expected:
        raise ModelBundleError("model bundle id does not match its signed contents")
    return payload


def verify_bundle_dir(
    bundle_dir: Path, *, public_key: str | bytes | None = None
) -> dict[str, Any]:
    try:
        manifest = json.loads((bundle_dir / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise ModelBundleError(f"cannot read model bundle manifest: {exc}") from exc
    payload = verify_bundle_manifest(manifest, public_key=public_key)
    if bundle_dir.name != payload["bundle_id"] and not bundle_dir.name.startswith("."):
        raise ModelBundleError("model bundle directory does not match bundle id")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ModelBundleError("model bundle manifest has no artifacts")
    allowed_names = set(required_artifact_names(payload.get("horizons") or []))
    observed_names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ModelBundleError("invalid model artifact entry")
        name = str(artifact.get("name", ""))
        if name not in allowed_names or Path(name).name != name:
            raise ModelBundleError(f"undeclared or unsafe model artifact name: {name}")
        path = bundle_dir / name
        if not path.is_file():
            raise ModelBundleError(f"model artifact is missing: {name}")
        if path.stat().st_size != int(artifact.get("bytes", -1)):
            raise ModelBundleError(f"model artifact size mismatch: {name}")
        if sha256_file(path) != artifact.get("sha256"):
            raise ModelBundleError(f"model artifact digest mismatch: {name}")
        observed_names.add(name)
    if observed_names != allowed_names:
        raise ModelBundleError("model bundle does not contain the exact required artifact set")
    return payload


def create_signed_control_pointer(
    *,
    bundle_id: str,
    previous_bundle_id: str | None,
    decision: Mapping[str, Any],
    private_key: str | bytes | None = None,
) -> dict[str, Any]:
    if not _BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ModelBundleError("invalid champion bundle id")
    if previous_bundle_id is not None and not _BUNDLE_ID_RE.fullmatch(previous_bundle_id):
        raise ModelBundleError("invalid previous champion bundle id")
    return _sign(
        {
            "schema": CONTROL_SCHEMA,
            "champion_bundle_id": bundle_id,
            "previous_bundle_id": previous_bundle_id,
            "promoted_at": _utc_now(),
            "decision": dict(decision),
        },
        private_key,
    )


def verify_control_pointer(
    pointer: Mapping[str, Any], *, public_key: str | bytes | None = None
) -> dict[str, Any]:
    payload = verify_signed_payload(pointer, public_key=public_key)
    if payload.get("schema") != CONTROL_SCHEMA:
        raise ModelBundleError("unsupported model control schema")
    bundle_id = str(payload.get("champion_bundle_id", ""))
    if not _BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ModelBundleError("control pointer has an invalid champion id")
    previous = payload.get("previous_bundle_id")
    if previous is not None and not _BUNDLE_ID_RE.fullmatch(str(previous)):
        raise ModelBundleError("control pointer has an invalid previous champion id")
    return payload


__all__ = [
    "BUNDLE_SCHEMA",
    "CONTROL_SCHEMA",
    "DEFAULT_HORIZONS",
    "DEFAULT_QUANTILES",
    "ModelBundleError",
    "create_signed_bundle",
    "create_signed_control_pointer",
    "required_artifact_names",
    "verify_bundle_dir",
    "verify_bundle_manifest",
    "verify_control_pointer",
    "verify_signed_payload",
]
