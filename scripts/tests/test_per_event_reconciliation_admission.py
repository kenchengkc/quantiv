"""Exercise serving admission and activation holds through saved evidence."""

import json
from datetime import date, datetime, timezone

import pytest

from ml.data_reconciliation import build_reconciliation_manifest
from scripts.data_release import build_release
from scripts.options_snapshot_resilience import finalize_snapshot
from scripts.recover_options_snapshot import require_accepted_candidate
from scripts.verify_refresh_recovery import verify_recovery
from scripts.verify_retrain_data_gate import verify_retrain_data_gate


def _candidate(data_dir, *, covered: int = 4, stale: bool = False):
    source_date = "2026-09-03" if stale else "2026-09-04"
    partition = data_dir / f"parquet/options_chain/year=2026/month=09/{source_date}.parquet"
    partition.parent.mkdir(parents=True, exist_ok=True)
    partition.write_bytes(b"immutable-test-options")
    manifest = build_reconciliation_manifest(
        generated_at="2026-09-06T12:01:00+00:00",
        datasets={"options": {"status": "passed", "lag_days": 1 if stale else 0,
                              "max_lag_days": 0}},
        event_coverage={"status": "failed", "expected_events": 10,
                        "covered_events": covered, "missing_events": 10 - covered,
                        "coverage_pct": covered / 10, "minimum_coverage_pct": 0.65},
        duplicates={"options": {"duplicate_rows": 0}},
        symbol_mappings={},
        corporate_actions={"continuity_status": "enforced"},
        pipeline_controls={"quarantine": {"status": "enforced"},
                           "idempotent_replay": {"status": "verified"}},
        quote_quality={"status": "failed" if stale else "passed",
                       "source_date": source_date, "expected_source_date": "2026-09-04",
                       "contracts": 100, "same_strike_pairs": 50,
                       "max_decision_group_rejection_rate": 0.5,
                       "errors": ["source session is stale"] if stale else []},
        source_reconciliation={"status": "passed", "source_date": source_date},
    )
    manifest_path = data_dir / "validation/data_reconciliation.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest))
    return partition, manifest_path, manifest


@pytest.mark.parametrize("covered", [1, 4, 6])
def test_current_sparse_snapshot_accepts_serving_and_recovery_but_holds_activation(
    tmp_path, covered,
):
    partition, manifest_path, manifest = _candidate(tmp_path, covered=covered)
    result = finalize_snapshot(manifest_path=manifest_path, data_dir=tmp_path,
                               not_before="2026-09-06T12:00:00+00:00")
    assert result.state == "accepted"
    assert result.can_score is True
    assert result.active_source_date == "2026-09-04"
    assert partition.read_bytes() == b"immutable-test-options"
    accepted = require_accepted_candidate(
        tmp_path, date(2026, 9, 4), "2026-09-06T12:00:00+00:00",
        now=datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc),
    )
    assert accepted["manifest_id"] == manifest["manifest_id"]
    _, _, release = build_release(tmp_path)
    restored = verify_recovery(tmp_path, expected_release_id=release["release_id"],
                               expected_manifest_id=manifest["manifest_id"])
    assert restored["options_state"] == "accepted"
    with pytest.raises(RuntimeError, match="model activation is blocked"):
        verify_retrain_data_gate(data_dir=tmp_path,
                                now=datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc))


def test_sparse_stale_snapshot_still_restores_published_fallback(tmp_path):
    previous = tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-02.parquet"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"published-options")
    build_release(tmp_path)
    partition, manifest_path, manifest = _candidate(tmp_path, stale=True)
    result = finalize_snapshot(manifest_path=manifest_path, data_dir=tmp_path,
                               not_before="2026-09-06T12:00:00+00:00")
    assert result.state == "fallback"
    assert result.can_score is False
    assert result.active_source_date == "2026-09-02"
    assert not partition.exists()
    assert previous.read_bytes() == b"published-options"
    assert manifest["quality"]["decision_safe"] is False
    assert manifest["quality"]["model_activation_eligible"] is False
