"""Signed immutable provenance retained with a frozen challenger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ml.evidence_receipt import verify_evidence_receipt
from ml.model_bundle import ModelBundleError, _sign, verify_signed_payload

SCHEMA = "quantiv.candidate-evidence.v1"


def verify_candidate_bindings(root: Path, bundle: dict[str, Any]) -> None:
    """Bind archived inputs to the receipts that authenticated the fitted trees."""
    receipt = verify_evidence_receipt(
        json.loads((root / "model_validation_receipt.json").read_text()), expected_scope="models"
    )
    if receipt["receipt_id"] != bundle["receipt_id"] or receipt["quality"]["status"] != "passed":
        raise ValueError("archived candidate validation receipt does not match signed bundle")
    model_bundles = [item for item in receipt["artifacts"] if item.get("name") == "model_bundle"]
    if len(model_bundles) != 1 or set(receipt["horizons"]) != set(bundle["horizons"]):
        raise ValueError("candidate validated model horizon/member set mismatch")
    validated_models = model_bundles[0]["members"]
    model_names = [Path(item["path"]).name for item in validated_models]
    signed_models = {item["name"]: item for item in bundle["artifacts"]}
    if len(model_names) != len(set(model_names)) or set(model_names) != set(signed_models):
        raise ValueError("candidate validated model member set mismatch")
    for item, name in zip(validated_models, model_names):
        if any(item[key] != signed_models[name][key] for key in ("bytes", "sha256")):
            raise ValueError(f"candidate model changed after validation: {name}")
    training_bundles = [item for item in receipt["artifacts"] if item.get("name") == "training_bundle"]
    if len(training_bundles) != 1:
        raise ValueError("candidate has no unique validated training bundle")
    members = training_bundles[0]["members"]
    names = [Path(item["path"]).name for item in members]
    expected = {f"{prefix}_T{horizon}.{suffix}" for horizon in receipt["horizons"]
                for prefix, suffix in (("training", "parquet"), ("metadata", "json"))}
    if len(names) != len(set(names)) or set(names) != expected:
        raise ValueError("candidate validated training member set mismatch")
    for item, name in zip(members, names):
        path = root / "training" / name
        if not path.is_file() or path.stat().st_size != item["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"candidate training changed after model validation: {name}")
    admission = json.loads((root / "historical_admission.json").read_text())
    if admission.get("schema") != "quantiv.historical-training-admission.v1" or admission.get("status") != "passed":
        raise ValueError("candidate inputs were not admitted for historical fitting")
    source = admission.get("calendar_source") or {}
    calendar_name = source.get("path")
    if calendar_name not in {"earnings_calendar.csv", "earnings_calendar.parquet"}:
        raise ValueError("candidate admitted calendar path is invalid")
    for name, digest in (("earnings_calendar.csv", admission.get("earnings_calendar_sha256")), (calendar_name, source.get("sha256"))):
        path = root / "inputs" / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"candidate calendar changed after historical admission: {name}")
    temporal = json.loads((root / "promotion/temporal_integrity.json").read_text())
    contract = temporal.get("feature_contract") or {}
    sources = {"feature_engineering.py": contract.get("sha256"), **contract.get("source_members", {})}
    for name, digest in sources.items():
        path = root / "source" / name
        if Path(name).name != name or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"candidate feature source digest changed after validation: {name}")


def sign_candidate_evidence(root: Path, bundle_id: str, *, private_key=None) -> dict[str, Any]:
    members = [
        {"path": path.relative_to(root).as_posix(),
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / "evidence.json"
    ]
    payload = _sign({"schema": SCHEMA, "bundle_id": bundle_id, "members": members}, private_key)
    (root / "evidence.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def verify_candidate_evidence(root: Path, bundle_id: str, *, public_key=None) -> dict[str, Any]:
    payload = verify_signed_payload(json.loads((root / "evidence.json").read_text()), public_key=public_key)
    if payload.get("schema") != SCHEMA or payload.get("bundle_id") != bundle_id:
        raise ModelBundleError("candidate evidence identity mismatch")
    observed = set()
    for item in payload.get("members", []):
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) in observed:
            raise ModelBundleError("unsafe candidate evidence path")
        observed.add(str(relative))
        path = root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ModelBundleError(f"candidate evidence digest mismatch: {relative}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*")
              if path.is_file() and path != root / "evidence.json"}
    if not observed or observed != actual:
        raise ModelBundleError("candidate evidence member set mismatch")
    return payload
