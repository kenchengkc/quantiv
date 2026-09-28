import json
from pathlib import Path

import pytest

from scripts.data_release import build_release
from scripts.verify_refresh_recovery import (
    RecoveryVerificationError,
    verify_recovery,
)


def _write_recovery_evidence(data_dir: Path, *, state: str = "accepted") -> str:
    parquet = data_dir / "parquet" / "options_chain" / "date=2026-09-25" / "part.parquet"
    parquet.parent.mkdir(parents=True, exist_ok=True)
    parquet.write_bytes(b"immutable-test-parquet")
    _, _, manifest = build_release(data_dir)
    release_id = manifest["release_id"]

    validation = data_dir / "validation"
    validation.mkdir(parents=True, exist_ok=True)
    reconciliation = {
        "schema": "quantiv.data-reconciliation.v2",
        "manifest_id": "sha256:test-reconciliation",
        "quality": {
            "status": "passed",
            "decision_safe": True,
            "critical_exceptions": 0,
            "warnings": 0,
        },
        "exceptions": [],
    }
    (validation / "data_reconciliation.json").write_text(json.dumps(reconciliation))

    accepted = state == "accepted"
    options = {
        "schema": "quantiv.options-snapshot-status.v1",
        "state": state,
        "active_source_date": "2026-09-25",
        "candidate_source_date": "2026-09-25",
        "candidate_manifest_id": "sha256:test-reconciliation",
        "critical_codes": [],
        "policy": {
            "scoring_allowed": accepted,
            "strict_options_candidate_accepted": accepted,
            "refresh_scoring_allowed": state in {"accepted", "fallback"},
            "fallback_mode": "last_published_snapshot" if state == "fallback" else None,
        },
    }
    (validation / "options_snapshot_status.json").write_text(json.dumps(options))
    return release_id


def test_recovery_accepts_exact_verified_release(tmp_path):
    release_id = _write_recovery_evidence(tmp_path)
    result = verify_recovery(
        tmp_path,
        expected_release_id=release_id,
        expected_manifest_id="sha256:test-reconciliation",
    )
    assert result["release_id"] == release_id
    assert result["options_state"] == "accepted"
    assert result["active_source_date"] == "2026-09-25"


def test_recovery_refuses_different_release_id(tmp_path):
    _write_recovery_evidence(tmp_path)
    with pytest.raises(RecoveryVerificationError, match="release mismatch"):
        verify_recovery(
            tmp_path,
            expected_release_id="sha256:not-the-release",
            expected_manifest_id="sha256:test-reconciliation",
        )


def test_recovery_refuses_blocked_options_state(tmp_path):
    release_id = _write_recovery_evidence(tmp_path, state="blocked")
    with pytest.raises(RecoveryVerificationError, match="not recoverable"):
        verify_recovery(
            tmp_path,
            expected_release_id=release_id,
            expected_manifest_id="sha256:test-reconciliation",
        )


def test_recovery_requires_release_pin(tmp_path):
    _write_recovery_evidence(tmp_path)
    with pytest.raises(RecoveryVerificationError, match="exact promoted data release id"):
        verify_recovery(
            tmp_path,
            expected_release_id="",
            expected_manifest_id="sha256:test-reconciliation",
        )


def test_recovery_refuses_different_reconciliation_manifest(tmp_path):
    release_id = _write_recovery_evidence(tmp_path)
    with pytest.raises(RecoveryVerificationError, match="reconciliation mismatch"):
        verify_recovery(
            tmp_path,
            expected_release_id=release_id,
            expected_manifest_id="sha256:wrong-manifest",
        )
