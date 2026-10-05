#!/usr/bin/env python3
"""Fail-closed entrypoint for Quantiv's model control plane.

The implementation lives in ``model_control_plane_impl.py``.  Keeping this
small wrapper makes promotion evidence mandatory without perturbing monitoring
or rollback behavior that is already covered by the existing control-plane
tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import model_control_plane_impl as _impl
from model_control_plane_impl import *  # noqa: F401,F403 - preserve script imports
from model_promotion_receipts import (
    DEFAULT_STATISTICAL_RECEIPT,
    DEFAULT_TEMPORAL_RECEIPT,
    attach_decision_evidence,
    verify_promotion_receipts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _option(name: str, default: str | None = None) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return default
    if index + 1 >= len(sys.argv):
        raise ValueError(f"{name} requires a value")
    return sys.argv[index + 1]


def _set_option(name: str, value: str) -> None:
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 >= len(sys.argv):
            raise ValueError(f"{name} requires a value")
        sys.argv[index + 1] = value
    else:
        sys.argv.extend([name, value])


def _resolve_registered_candidate() -> None:
    """Bind receipt verification and scoring to the same verified frozen bundle."""
    models_root = Path(str(_option("--models-root", str(REPO_ROOT / "data/models"))))
    _, registry = _impl._control_state(models_root)
    frozen = registry.get("challenger_bundle_id")
    if not frozen:
        return
    bundle_dir = models_root / "bundles" / str(frozen)
    manifest = _impl.verify_bundle_dir(bundle_dir)
    metadata = _impl._read_json(
        bundle_dir / f"metadata_T{int(manifest['horizons'][0])}.json"
    )
    if _impl.feature_protocol(
        metadata
    ) != "quantiv.earnings-causal.v2" or not _impl._has_complete_candidate_evidence(
        models_root, str(frozen), manifest["horizons"]
    ):
        return  # Registration may migrate a legacy or unverifiable designation.
    report = Path(
        str(_option("--report", str(REPO_ROOT / "data/validation/model_decision.json")))
    )
    record = report.parent / "evaluation_candidate.json"
    _impl._atomic_json(
        record,
        {
            "bundle_id": frozen,
            "bundle_dir": str(bundle_dir),
            "receipt_id": manifest["receipt_id"],
        },
    )
    _set_option("--candidate-manifest", str(record))
    _set_option(
        "--archived-evidence-dir", str(models_root / "candidates" / str(frozen))
    )


def _validate_decision_evidence() -> dict:
    candidate_path = Path(str(_option("--candidate-manifest")))
    candidate = json.loads(candidate_path.read_text())
    bundle_id = str(candidate.get("bundle_id") or "")
    training_dir = Path(
        str(_option("--training-dir", str(REPO_ROOT / "data" / "ml_training")))
    )
    archived = _option("--archived-evidence-dir")
    if archived:
        archive = Path(archived)
        return verify_promotion_receipts(
            bundle_id,
            training_dir=archive / "training",
            temporal_path=archive / "promotion" / "temporal_integrity.json",
            statistical_path=archive / "promotion" / "statistical_selection.json",
            archived_evidence_dir=archive,
        )
    return verify_promotion_receipts(
        bundle_id,
        training_dir=training_dir,
        temporal_path=DEFAULT_TEMPORAL_RECEIPT,
        statistical_path=DEFAULT_STATISTICAL_RECEIPT,
    )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "decide":
        _resolve_registered_candidate()
        gate_path = _option("--activation-gate-report")
        if not gate_path:
            raise ValueError(
                "--activation-gate-report is required for production decisions"
            )
        gate = json.loads(Path(gate_path).read_text())
        # Retention remains possible when live data are held; activation still requires receipts.
        evidence = (
            _validate_decision_evidence() if gate.get("status") == "passed" else None
        )
        result = _impl.main()
        if result == 0 and evidence is not None:
            report_path = Path(
                str(
                    _option(
                        "--report",
                        str(REPO_ROOT / "data" / "validation" / "model_decision.json"),
                    )
                )
            )
            attach_decision_evidence(report_path, evidence)
        return result
    return _impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
