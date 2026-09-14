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


def _validate_decision_evidence() -> dict:
    candidate_path = Path(str(_option("--candidate-manifest")))
    candidate = json.loads(candidate_path.read_text())
    bundle_id = str(candidate.get("bundle_id") or "")
    training_dir = Path(
        str(_option("--training-dir", str(REPO_ROOT / "data" / "ml_training")))
    )
    return verify_promotion_receipts(
        bundle_id,
        training_dir=training_dir,
        temporal_path=DEFAULT_TEMPORAL_RECEIPT,
        statistical_path=DEFAULT_STATISTICAL_RECEIPT,
    )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "decide":
        evidence = _validate_decision_evidence()
        result = _impl.main()
        if result == 0:
            report_path = Path(
                str(_option("--report", str(REPO_ROOT / "data" / "validation" / "model_decision.json")))
            )
            attach_decision_evidence(report_path, evidence)
        return result
    return _impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
