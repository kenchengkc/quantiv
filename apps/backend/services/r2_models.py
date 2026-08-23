"""Atomically install signed, content-addressed model bundles from R2."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ml.model_artifact import sha256_file
from ml.model_bundle import (
    ModelBundleError,
    verify_bundle_dir,
    verify_bundle_manifest,
    verify_control_pointer,
)

logger = logging.getLogger(__name__)

R2_MODEL_PREFIX = "models/"
CONTROL_KEY = f"{R2_MODEL_PREFIX}control/champion.json"


@dataclass(frozen=True)
class ModelSyncResult:
    bundle_id: str | None
    downloaded_files: int
    activated: bool
    models_dir: Path | None
    error: str | None = None


def _build_client():
    try:
        import boto3
    except ImportError:
        logger.info("boto3 not installed; R2 model sync disabled")
        return None

    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    if not (account_id and access_key and secret_key):
        logger.info("R2 credentials not set; R2 model sync disabled")
        return None
    endpoint_url = os.getenv("R2_ENDPOINT") or (
        f"https://{account_id}.r2.cloudflarestorage.com"
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def _get_json(client: Any, bucket: str, key: str) -> dict[str, Any]:
    response = client.get_object(Bucket=bucket, Key=key)
    payload = json.loads(response["Body"].read())
    if not isinstance(payload, dict):
        raise ModelBundleError(f"R2 object is not a JSON object: {key}")
    return payload


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ModelBundleError("model control timestamp is missing")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _reject_pointer_replay(target_dir: Path, incoming: Mapping[str, Any]) -> None:
    accepted_path = target_dir / "control" / "champion.json"
    if not accepted_path.exists():
        return
    accepted = verify_control_pointer(json.loads(accepted_path.read_text()))
    incoming_time = _parse_timestamp(incoming.get("promoted_at"))
    accepted_time = _parse_timestamp(accepted.get("promoted_at"))
    if incoming_time < accepted_time:
        raise ModelBundleError("refusing a model control pointer replay")
    if incoming_time == accepted_time and incoming != accepted:
        raise ModelBundleError("conflicting model control pointers have the same timestamp")


def _atomic_activate(target_dir: Path, bundle_id: str, pointer: Mapping[str, Any]) -> Path:
    current = target_dir / "current"
    temporary_link = target_dir / f".current-{bundle_id[:12]}.tmp"
    temporary_link.unlink(missing_ok=True)
    temporary_link.symlink_to(Path("versions") / bundle_id, target_is_directory=True)
    temporary_link.replace(current)

    control_dir = target_dir / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    pointer_path = control_dir / "champion.json"
    temporary_pointer = control_dir / ".champion.json.tmp"
    temporary_pointer.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
    temporary_pointer.replace(pointer_path)
    return current.resolve(strict=True)


def _download_bundle(
    client: Any,
    bucket: str,
    target_dir: Path,
    bundle_id: str,
    manifest: Mapping[str, Any],
) -> tuple[Path, int]:
    versions = target_dir / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    destination = versions / bundle_id
    if destination.exists():
        try:
            verify_bundle_dir(destination)
            return destination, 0
        except ModelBundleError:
            quarantine = versions / f".quarantine-{bundle_id}-{int(datetime.now().timestamp())}"
            destination.replace(quarantine)
            logger.error("Quarantined a corrupted local model bundle")

    payload = verify_bundle_manifest(manifest)
    temporary = Path(tempfile.mkdtemp(prefix=f".{bundle_id[:12]}-", dir=versions))
    downloaded = 0
    try:
        for artifact in payload["artifacts"]:
            name = artifact["name"]
            local_path = temporary / name
            remote_key = f"{R2_MODEL_PREFIX}bundles/{bundle_id}/{name}"
            client.download_file(bucket, remote_key, str(local_path))
            downloaded += 1
            if local_path.stat().st_size != int(artifact["bytes"]):
                raise ModelBundleError(f"downloaded model size mismatch: {name}")
            if sha256_file(local_path) != artifact["sha256"]:
                raise ModelBundleError(f"downloaded model digest mismatch: {name}")
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        verify_bundle_dir(temporary)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, downloaded


def sync_models_from_r2(target_dir: Path) -> ModelSyncResult:
    """Verify, install, and activate one complete champion bundle.

    No downloaded artifact becomes visible to inference until the signed pointer,
    signed manifest, exact artifact set, byte sizes, and SHA-256 digests all pass.
    A failed transfer leaves the previously verified champion active.
    """
    bucket = os.getenv("R2_BUCKET")
    if not bucket:
        return ModelSyncResult(None, 0, False, None, "R2_BUCKET is not configured")
    client = _build_client()
    if client is None:
        return ModelSyncResult(None, 0, False, None, "R2 client is not configured")

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        pointer_envelope = _get_json(client, bucket, CONTROL_KEY)
        pointer = verify_control_pointer(pointer_envelope)
        _reject_pointer_replay(target_dir, pointer)
        bundle_id = pointer["champion_bundle_id"]
        manifest_key = f"{R2_MODEL_PREFIX}bundles/{bundle_id}/manifest.json"
        manifest = _get_json(client, bucket, manifest_key)
        manifest_payload = verify_bundle_manifest(manifest)
        if manifest_payload["bundle_id"] != bundle_id:
            raise ModelBundleError("control pointer and bundle manifest disagree")
        _, downloaded = _download_bundle(
            client, bucket, target_dir, bundle_id, manifest
        )
        models_dir = _atomic_activate(target_dir, bundle_id, pointer_envelope)
        logger.info(
            "Activated verified model bundle %s (%d downloaded files)",
            bundle_id[:12],
            downloaded,
        )
        return ModelSyncResult(bundle_id, downloaded, True, models_dir)
    except Exception as exc:
        logger.error("Signed R2 model sync rejected: %s", type(exc).__name__)
        return ModelSyncResult(None, 0, False, None, str(exc))


def active_models_dir(target_dir: Path) -> Path | None:
    current = target_dir / "current"
    if not current.exists():
        return None
    try:
        resolved = current.resolve(strict=True)
        verify_bundle_dir(resolved)
        return resolved
    except (OSError, ModelBundleError, ValueError):
        return None


def configured() -> bool:
    return bool(
        os.getenv("R2_BUCKET")
        and os.getenv("R2_ACCOUNT_ID")
        and os.getenv("R2_ACCESS_KEY_ID")
        and os.getenv("R2_SECRET_ACCESS_KEY")
    )


__all__ = [
    "ModelSyncResult",
    "active_models_dir",
    "configured",
    "sync_models_from_r2",
]
