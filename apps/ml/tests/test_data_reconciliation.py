from __future__ import annotations

from ml.data_reconciliation import build_reconciliation_manifest


def _manifest(generated_at: str, *, duplicate_rows: int = 0) -> dict:
    return build_reconciliation_manifest(
        generated_at=generated_at,
        datasets={
            "options": {
                "status": "passed",
                "rows": 100,
                "lag_days": 1,
                "max_lag_days": 5,
            }
        },
        event_coverage={
            "expected_events": 10,
            "covered_events": 8,
            "missing_events": 2,
            "missing_sample": [{"symbol": "MISS", "earnings_date": "2026-08-25"}],
        },
        duplicates={"options": {"duplicate_rows": duplicate_rows, "affected_dates": 0}},
        symbol_mappings={
            "rename_rules": 7,
            "retired_symbols": 20,
            "stale_source_symbols": [],
        },
        corporate_actions={
            "rows": 12,
            "symbols": 4,
            "continuity_status": "observed_only",
        },
        pipeline_controls={
            "quarantine": {"status": "not_instrumented", "mode": "fail_closed"},
            "idempotent_replay": {"status": "contract_only"},
        },
    )


def test_manifest_is_reproducible_and_surfaces_instrumentation_gaps() -> None:
    first = _manifest("2026-08-22T12:00:00+00:00")
    second = _manifest("2026-08-22T13:00:00+00:00")

    assert first["manifest_id"] == second["manifest_id"]
    assert first["quality"] == {
        "status": "degraded",
        "decision_safe": True,
        "critical_exceptions": 0,
        "warnings": 4,
    }
    assert {issue["code"] for issue in first["exceptions"]} == {
        "upcoming_events_without_option_chain",
        "corporate_action_continuity_not_enforced",
        "record_quarantine_not_instrumented",
        "idempotent_replay_not_verified",
    }


def test_duplicate_serving_keys_make_manifest_fail_closed() -> None:
    clean = _manifest("2026-08-22T12:00:00+00:00")
    failed = _manifest("2026-08-22T12:00:00+00:00", duplicate_rows=3)

    assert failed["manifest_id"] != clean["manifest_id"]
    assert failed["quality"]["status"] == "failed"
    assert failed["quality"]["decision_safe"] is False
    assert failed["quality"]["critical_exceptions"] == 1
    assert "options_duplicate_keys" in {issue["code"] for issue in failed["exceptions"]}
