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
from ml.evidence_receipt import verify_evidence_receipt

BUNDLE_SCHEMA = "quantiv.model-bundle.v1"
CONTROL_SCHEMA = "quantiv.model-control.v1"
REGISTRY_SCHEMA = "quantiv.model-registry.v1"
MONITOR_SCHEMA = "quantiv.model-monitor.v1"
OUTCOME_SCHEMA = "quantiv.model-outcome-receipt.v1"
SIGNATURE_ALGORITHM = "ed25519"
DEFAULT_HORIZONS = (1, 2, 3, 7, 14, 21)
# Daily generation covers 21 upcoming days plus a four-day completed-close lag.
DAILY_HORIZONS = tuple(range(1, 26))
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
    serving_capabilities: Mapping[str, Any] | None = None,
    historical_admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = {
        "artifacts": artifacts,
        "receipt_id": receipt_id,
        "horizons": sorted(set(horizons)),
        "source_revision": source_revision,
    }
    if serving_capabilities is not None:
        identity["serving_capabilities"] = dict(serving_capabilities)
    if historical_admission is not None:
        identity["historical_admission"] = dict(historical_admission)
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
    try:
        report = json.loads(validation_report_path.read_text())
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as exc:
        raise ModelBundleError(f"cannot read model validation evidence: {exc}") from exc
    if not isinstance(report, Mapping) or report.get("status") != "passed":
        raise ModelBundleError("refusing to package a model set that did not pass validation")
    try:
        if not isinstance(receipt, Mapping):
            raise ValueError("model validation receipt must be an object")
        receipt = verify_evidence_receipt(receipt, expected_scope="models")
        if receipt["quality"].get("status") != "passed":
            raise ValueError("model validation receipt did not pass validation")
        if "evidence_receipt" in report:
            reported_receipt = verify_evidence_receipt(report["evidence_receipt"], expected_scope="models")
            if reported_receipt["receipt_id"] != receipt["receipt_id"]:
                raise ValueError("validation report and model receipt identities differ")
        expected_horizons = sorted(set(horizons))
        receipt_horizons = receipt["horizons"]
        if (
            not expected_horizons
            or any(type(horizon) is not int or horizon <= 0 for horizon in expected_horizons)
            or any(type(horizon) is not int for horizon in receipt_horizons)
            or sorted(receipt_horizons) != expected_horizons
        ):
            raise ValueError("validated model horizons differ from requested bundle horizons")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ModelBundleError(f"invalid model validation receipt: {exc}") from exc
    receipt_id = receipt["receipt_id"]

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
    try:
        model_bundles = [item for item in receipt["artifacts"] if item.get("name") == "model_bundle"]
        if len(model_bundles) != 1:
            raise ValueError("model receipt has no unique validated model bundle")
        members = model_bundles[0]["members"]
        member_names = [Path(item["path"]).name for item in members]
        if len(member_names) != len(set(member_names)) or set(member_names) != set(names):
            raise ValueError("validated model member set differs from requested bundle")
        validated = dict(zip(member_names, members))
        for artifact in artifacts:
            if any(validated[artifact["name"]][key] != artifact[key] for key in ("bytes", "sha256")):
                raise ValueError(f"model changed after validation: {artifact['name']}")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ModelBundleError(f"invalid model validation receipt: {exc}") from exc
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
    horizons = payload.get("horizons")
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(type(horizon) is not int or horizon not in DAILY_HORIZONS for horizon in horizons)
        or len(set(horizons)) != len(horizons)
    ):
        raise ModelBundleError("signed model bundle has invalid or unsupported horizons")
    if ("serving_capabilities" in payload) != ("historical_admission" in payload):
        raise ModelBundleError("serving capabilities require bound historical admission")
    if "historical_admission" in payload and (
        not isinstance(payload["historical_admission"], Mapping)
        or not isinstance(payload["serving_capabilities"], Mapping)
    ):
        raise ModelBundleError("historical admission and serving capabilities must be objects")
    bundle_id = str(payload.get("bundle_id", ""))
    if not _BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ModelBundleError("invalid model bundle id")
    expected = _bundle_core(
        artifacts=list(payload.get("artifacts") or []),
        receipt_id=str(payload.get("receipt_id", "")),
        horizons=list(payload.get("horizons") or []),
        source_revision=str(payload.get("source_revision", "")),
        created_at=str(payload.get("created_at", "")),
        serving_capabilities=payload.get("serving_capabilities"),
        historical_admission=payload.get("historical_admission"),
    )["bundle_id"]
    if bundle_id != expected:
        raise ModelBundleError("model bundle id does not match its signed contents")
    if "historical_admission" in payload:
        _verify_historical_bundle(payload, public_key=public_key)
    return payload


def _artifact_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ModelBundleError("model bundle artifacts must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for artifact in artifacts:
        if (not isinstance(artifact, Mapping) or not isinstance(artifact.get("name"), str)
                or artifact["name"] in result or type(artifact.get("bytes")) is not int
                or artifact["bytes"] < 0 or not isinstance(artifact.get("sha256"), str)
                or not _BUNDLE_ID_RE.fullmatch(artifact["sha256"])):
            raise ModelBundleError("invalid or duplicate signed model artifact")
        result[artifact["name"]] = artifact
    if set(result) != set(required_artifact_names(payload["horizons"])):
        raise ModelBundleError("signed model artifact set differs from exact declared horizons")
    return result


def _verify_historical_bundle(
    payload: Mapping[str, Any], *, public_key: str | bytes | None,
) -> None:
    from ml.historical_admission import verify_historical_admission_receipt
    from ml.serving_capabilities import validate_serving_capabilities

    admission = payload["historical_admission"]
    try:
        if not isinstance(admission, Mapping) or set(admission) != {"source_manifest", "receipt"}:
            raise ValueError("historical admission must bind the source manifest and receipt")
        source = admission["source_manifest"]
        if not isinstance(source, Mapping) or "historical_admission" in source or "serving_capabilities" in source:
            raise ValueError("historical admission source must be an original model bundle")
        parent = verify_bundle_manifest(source, public_key=public_key)
        receipt = verify_historical_admission_receipt(
            admission["receipt"], bundle_manifest=parent, public_key=public_key,
        )
        capabilities = validate_serving_capabilities(payload["serving_capabilities"], payload["horizons"])
        admitted = validate_serving_capabilities(receipt["admitted_cohorts"], payload["horizons"])
        if capabilities != admitted or payload["receipt_id"] != parent["receipt_id"]:
            raise ValueError("historical admission differs from serving capabilities or model receipt")
        parent_artifacts = _artifact_map(parent)
        if not set(payload["horizons"]).issubset(parent["horizons"]):
            raise ValueError("admitted model heads exceed the historical source bundle")
        for name, artifact in _artifact_map(payload).items():
            if artifact != parent_artifacts.get(name):
                raise ValueError("admitted model artifact differs from the historical source bundle")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ModelBundleError(f"invalid historical model admission: {exc}") from exc


def create_historically_admitted_bundle(
    source_bundle_dir: Path,
    bundle_root: Path,
    *,
    admission_receipt: Mapping[str, Any],
    source_revision: str,
    private_key: str | bytes | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Sign only proven head/cohort combinations without changing trained bytes."""
    from ml.historical_admission import verify_historical_admission_receipt
    from ml.serving_capabilities import validate_serving_capabilities

    public_key = _public_from_private(private_key)
    parent = verify_bundle_dir(source_bundle_dir, public_key=public_key)
    if "historical_admission" in parent:
        raise ModelBundleError("historically admitted bundles cannot be derived again")
    try:
        receipt = verify_historical_admission_receipt(
            admission_receipt, bundle_manifest=parent, public_key=public_key,
        )
        horizons = sorted(int(value) for value in receipt["admitted_cohorts"])
        if not horizons:
            raise ValueError("historical admission has no qualifying model heads")
        capabilities = validate_serving_capabilities(receipt["admitted_cohorts"], horizons)
        if not set(horizons).issubset(parent["horizons"]):
            raise ValueError("historical admission exceeds the source model heads")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ModelBundleError(f"invalid historical model admission: {exc}") from exc
    source_manifest = json.loads((source_bundle_dir / "manifest.json").read_text())
    parent_artifacts = _artifact_map(parent)
    core = _bundle_core(
        artifacts=[dict(parent_artifacts[name]) for name in required_artifact_names(horizons)],
        receipt_id=parent["receipt_id"], horizons=horizons,
        source_revision=source_revision, created_at=_utc_now(),
        serving_capabilities=capabilities,
        historical_admission={"source_manifest": source_manifest, "receipt": dict(admission_receipt)},
    )
    manifest = _sign(core, private_key)
    verify_bundle_manifest(manifest, public_key=public_key)
    destination = bundle_root / core["bundle_id"]
    if destination.exists():
        verify_bundle_dir(destination, public_key=public_key)
        return destination, manifest
    bundle_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{core['bundle_id'][:12]}-", dir=bundle_root))
    try:
        for artifact in core["artifacts"]:
            shutil.copy2(source_bundle_dir / artifact["name"], temporary / artifact["name"])
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        verify_bundle_dir(temporary, public_key=public_key)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, manifest


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
    if "serving_capabilities" in payload:
        from ml.serving_capabilities import allowed_cohorts
        for horizon in payload["horizons"]:
            try:
                metadata = json.loads((bundle_dir / f"metadata_T{horizon}.json").read_text())
                allowed_cohorts(payload, horizon, metadata)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ModelBundleError(f"invalid admitted model metadata: {exc}") from exc
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


def create_signed_registry(
    *,
    champion_bundle_id: str,
    challenger_bundle_id: str | None,
    previous_bundle_id: str | None,
    decision: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]] = (),
    private_key: str | bytes | None = None,
) -> dict[str, Any]:
    for label, bundle_id in (
        ("champion", champion_bundle_id),
        ("challenger", challenger_bundle_id),
        ("previous", previous_bundle_id),
    ):
        if bundle_id is not None and not _BUNDLE_ID_RE.fullmatch(bundle_id):
            raise ModelBundleError(f"invalid {label} bundle id")
    return _sign(
        {
            "schema": REGISTRY_SCHEMA,
            "champion_bundle_id": champion_bundle_id,
            "challenger_bundle_id": challenger_bundle_id,
            "previous_bundle_id": previous_bundle_id,
            "updated_at": _utc_now(),
            "decision": dict(decision),
            "history": [dict(item) for item in list(history)[-20:]],
        },
        private_key,
    )


def verify_registry(
    registry: Mapping[str, Any], *, public_key: str | bytes | None = None
) -> dict[str, Any]:
    payload = verify_signed_payload(registry, public_key=public_key)
    if payload.get("schema") != REGISTRY_SCHEMA:
        raise ModelBundleError("unsupported model registry schema")
    for label, key in (
        ("champion", "champion_bundle_id"),
        ("challenger", "challenger_bundle_id"),
        ("previous", "previous_bundle_id"),
    ):
        bundle_id = payload.get(key)
        if bundle_id is not None and not _BUNDLE_ID_RE.fullmatch(str(bundle_id)):
            raise ModelBundleError(f"registry has an invalid {label} bundle id")
    if not isinstance(payload.get("history"), list):
        raise ModelBundleError("registry history must be a list")
    return payload


def resolve_champion_bundle(models_root: Path) -> Path:
    pointer_path = models_root / "control" / "champion.json"
    if not pointer_path.exists():
        raise ModelBundleError("signed champion pointer does not exist")
    try:
        envelope = json.loads(pointer_path.read_text())
    except (OSError, ValueError) as exc:
        raise ModelBundleError("cannot read signed champion pointer") from exc
    pointer = verify_control_pointer(envelope)
    bundle_dir = models_root / "bundles" / pointer["champion_bundle_id"]
    verify_bundle_dir(bundle_dir)
    return bundle_dir


def create_signed_monitor_receipt(
    *,
    ledger_path: Path,
    report_path: Path,
    snapshot_date: str,
    private_key: str | bytes | None = None,
) -> dict[str, Any]:
    return _sign(
        {
            "schema": MONITOR_SCHEMA,
            "snapshot_date": snapshot_date,
            "created_at": _utc_now(),
            "ledger": {
                "name": ledger_path.name,
                "bytes": ledger_path.stat().st_size,
                "sha256": sha256_file(ledger_path),
            },
            "report": {
                "name": report_path.name,
                "bytes": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            },
        },
        private_key,
    )


def verify_monitor_receipt(
    receipt: Mapping[str, Any],
    *,
    ledger_path: Path,
    report_path: Path,
    public_key: str | bytes | None = None,
) -> dict[str, Any]:
    payload = verify_signed_payload(receipt, public_key=public_key)
    if payload.get("schema") != MONITOR_SCHEMA:
        raise ModelBundleError("unsupported model monitoring receipt schema")
    for label, path in (("ledger", ledger_path), ("report", report_path)):
        artifact = payload.get(label)
        if not isinstance(artifact, Mapping):
            raise ModelBundleError(f"monitoring receipt has no {label} artifact")
        if artifact.get("name") != path.name:
            raise ModelBundleError(f"monitoring {label} name mismatch")
        if int(artifact.get("bytes", -1)) != path.stat().st_size:
            raise ModelBundleError(f"monitoring {label} size mismatch")
        if artifact.get("sha256") != sha256_file(path):
            raise ModelBundleError(f"monitoring {label} digest mismatch")
    return payload


def create_signed_outcome_receipt(
    *,
    report_path: Path,
    history_path: Path,
    private_key: str | bytes | None = None,
) -> dict[str, Any]:
    """Sign the latest realized-outcome report and its bounded history."""
    return _sign(
        {
            "schema": OUTCOME_SCHEMA,
            "created_at": _utc_now(),
            "report": {
                "name": report_path.name,
                "bytes": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            },
            "history": {
                "name": history_path.name,
                "bytes": history_path.stat().st_size,
                "sha256": sha256_file(history_path),
            },
        },
        private_key,
    )


def verify_outcome_receipt(
    receipt: Mapping[str, Any],
    *,
    report_path: Path,
    history_path: Path,
    public_key: str | bytes | None = None,
) -> dict[str, Any]:
    """Fail closed when realized-outcome evidence is missing or altered."""
    payload = verify_signed_payload(receipt, public_key=public_key)
    if payload.get("schema") != OUTCOME_SCHEMA:
        raise ModelBundleError("unsupported model outcome receipt schema")
    for label, path in (("report", report_path), ("history", history_path)):
        artifact = payload.get(label)
        if not isinstance(artifact, Mapping):
            raise ModelBundleError(f"outcome receipt has no {label} artifact")
        if artifact.get("name") != path.name:
            raise ModelBundleError(f"outcome {label} name mismatch")
        if int(artifact.get("bytes", -1)) != path.stat().st_size:
            raise ModelBundleError(f"outcome {label} size mismatch")
        if artifact.get("sha256") != sha256_file(path):
            raise ModelBundleError(f"outcome {label} digest mismatch")
    return payload


__all__ = [
    "BUNDLE_SCHEMA",
    "CONTROL_SCHEMA",
    "REGISTRY_SCHEMA",
    "MONITOR_SCHEMA",
    "OUTCOME_SCHEMA",
    "DEFAULT_HORIZONS",
    "DAILY_HORIZONS",
    "DEFAULT_QUANTILES",
    "ModelBundleError",
    "create_signed_bundle",
    "create_historically_admitted_bundle",
    "create_signed_control_pointer",
    "create_signed_monitor_receipt",
    "create_signed_outcome_receipt",
    "create_signed_registry",
    "required_artifact_names",
    "resolve_champion_bundle",
    "verify_bundle_dir",
    "verify_bundle_manifest",
    "verify_control_pointer",
    "verify_monitor_receipt",
    "verify_outcome_receipt",
    "verify_registry",
    "verify_signed_payload",
]
