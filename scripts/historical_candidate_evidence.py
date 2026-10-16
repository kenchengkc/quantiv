"""Replay retained historical evidence before a derived bundle can be promoted."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from ml.candidate_evidence import verify_candidate_bindings, verify_candidate_evidence
from ml.historical_admission import verify_historical_admission_receipt
from ml.model_bundle import verify_bundle_dir
from ml.serving_capabilities import validate_serving_capabilities
from model_promotion_receipts import verify_promotion_receipts


def verify_historical_candidate(models_root: Path, bundle_dir: Path) -> dict:
    bundle = verify_bundle_dir(bundle_dir)
    admission = bundle.get("historical_admission")
    if not isinstance(admission, dict):
        raise ValueError("candidate has no authenticated historical admission")
    source_id = admission["source_manifest"]["bundle_id"]
    source_dir = models_root / "bundles" / source_id
    source = verify_bundle_dir(source_dir)
    if json.loads((source_dir / "manifest.json").read_text()) != admission["source_manifest"]:
        raise ValueError("retained source manifest differs from the tested source")
    archive = models_root / "candidates" / source_id
    verify_candidate_evidence(archive, source_id)
    verify_candidate_bindings(archive, source)
    promotion = verify_promotion_receipts(
        source_id, training_dir=archive / "training",
        temporal_path=archive / "promotion/temporal_integrity.json",
        statistical_path=archive / "promotion/statistical_selection.json",
        archived_evidence_dir=archive,
    )
    evidence = verify_historical_admission_receipt(
        admission["receipt"], bundle_dir=source_dir,
        evidence_root=models_root / "historical_admissions" / source_id,
    )
    source_admission = archive / "historical_admission.json"
    if evidence.get("source_admission", {}).get("sha256") != hashlib.sha256(source_admission.read_bytes()).hexdigest():
        raise ValueError("historical test source admission differs from the retained fitting source")
    admitted = validate_serving_capabilities(evidence["admitted_cohorts"], bundle["horizons"])
    capabilities = validate_serving_capabilities(bundle["serving_capabilities"], bundle["horizons"])
    if evidence["status"] != "passed" or admitted != capabilities:
        raise ValueError("historical evidence does not authorize the serving candidate")
    return {**evidence, "source_promotion_evidence": promotion}
