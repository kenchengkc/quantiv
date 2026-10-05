#!/usr/bin/env python3
"""Archive one signed candidate without touching production discovery pointers."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/ml"))
from ml.model_bundle import verify_bundle_dir  # noqa: E402
from ml.candidate_evidence import sign_candidate_evidence, verify_candidate_evidence, verify_candidate_bindings  # noqa: E402
from ml.evidence_receipt import verify_evidence_receipt  # noqa: E402
from model_promotion_receipts import verify_promotion_receipts  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def prepare_candidate_evidence(*, bundle_dir: Path, data_dir: Path) -> Path:
    bundle = verify_bundle_dir(bundle_dir)
    root = data_dir / "models/candidates" / bundle["bundle_id"]
    if root.exists():
        verify_candidate_evidence(root, bundle["bundle_id"])
        verify_candidate_bindings(root, bundle)
        return root
    receipt = verify_evidence_receipt(json.loads((data_dir / "models/receipts/latest_models.json").read_text()), expected_scope="models")
    if receipt["receipt_id"] != bundle["receipt_id"] or receipt["quality"]["status"] != "passed":
        raise ValueError("candidate archive validation receipt does not match signed bundle")
    temporary = root.with_name(f".{root.name}.pending")
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        shutil.copytree(data_dir / "ml_training", temporary / "training")
        shutil.copytree(data_dir / "validation/promotion", temporary / "promotion")
        source = temporary / "source"
        source.mkdir(parents=True)
        for relative in ("apps/ml/feature_engineering.py", "apps/ml/ml/causal_features.py", "apps/ml/ml/corporate_actions.py", "apps/ml/ml/model_protocol.py", "config/market_sessions.json"):
            path = REPO_ROOT / relative
            if path.exists():
                shutil.copyfile(path, source / path.name)
        shutil.copyfile(data_dir / "models/receipts/latest_models.json", temporary / "model_validation_receipt.json")
        shutil.copyfile(data_dir / "validation/historical_training_admission.json", temporary / "historical_admission.json")
        inputs = temporary / "inputs"
        inputs.mkdir()
        for name in ("earnings_calendar.csv", "earnings_calendar.parquet"):
            path = data_dir / name
            if path.is_file():
                shutil.copyfile(path, inputs / name)
        verify_candidate_bindings(temporary, bundle)
        verify_promotion_receipts(
            bundle["bundle_id"], training_dir=temporary / "training",
            temporal_path=temporary / "promotion/temporal_integrity.json",
            statistical_path=temporary / "promotion/statistical_selection.json",
        )
        sign_candidate_evidence(temporary, bundle["bundle_id"])
        root.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(root)
        verify_candidate_evidence(root, bundle["bundle_id"])
        return root
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def archive_candidate(*, bundle_dir: Path, remote: str, evidence_dir: Path | None = None) -> dict[str, Any]:
    bundle = verify_bundle_dir(bundle_dir)
    bundle_id = bundle["bundle_id"]
    if evidence_dir:
        verify_candidate_evidence(evidence_dir, bundle_id)
        verify_candidate_bindings(evidence_dir, bundle)
    destination = f"{remote.rstrip('/')}/models/bundles/{bundle_id}"
    subprocess.run(
        ["rclone", "copy", str(bundle_dir), destination, "--immutable"], check=True
    )
    if evidence_dir:
        evidence_destination = f"{remote.rstrip('/')}/models/candidates/{bundle_id}"
        subprocess.run(["rclone", "copy", str(evidence_dir), evidence_destination, "--immutable"], check=True)
        subprocess.run(["rclone", "check", str(evidence_dir), evidence_destination, "--one-way"], check=True)
    subprocess.run(
        ["rclone", "check", str(bundle_dir), destination, "--one-way"], check=True
    )
    return {"status": "passed", "bundle_id": bundle_id, "remote": destination}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--retain-evidence", action="store_true")
    args = parser.parse_args()
    remote = os.getenv("R2_REMOTE") or f"r2:{os.getenv('R2_BUCKET') or 'quantiv-data'}"
    evidence = prepare_candidate_evidence(bundle_dir=args.bundle_dir, data_dir=args.data_dir) if args.retain_evidence else None
    result = archive_candidate(bundle_dir=args.bundle_dir, remote=remote, evidence_dir=evidence)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Archived signed candidate {result['bundle_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
