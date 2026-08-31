from build_control_plane_snapshot import (
    build_snapshot,
    build_workflow_reference,
    update_history,
)


def test_snapshot_is_compact_and_does_not_expose_artifact_ids() -> None:
    snapshot = build_snapshot(
        {
            "quality": {"status": "degraded", "decision_safe": True},
            "source_reconciliation": {"source_date": "2026-08-28"},
            "quote_quality": {
                "status": "passed",
                "source_session_lag": 0,
                "contract_rejection_rate": 0.12,
                "pair_rejection_rate": 0.03,
                "live_trading_eligible": False,
            },
            "event_coverage": {"coverage_pct": 0.98, "missing_events": 2},
            "pipeline_controls": {
                "quarantine": {"status": "enforced", "records": 42},
                "idempotent_replay": {"status": "verified"},
            },
            "exceptions": [
                {
                    "code": "upcoming_events_without_option_chain",
                    "severity": "warning",
                    "summary": "Two events lack an eligible chain",
                    "count": 2,
                    "sample": [{"symbol": "SECRET_SAMPLE"}],
                }
            ],
        },
        {
            "status": "passed",
            "monitored_at": "2026-08-28T12:00:00Z",
            "feature_drift": {"status": "passed", "critical_features": 0},
            "shadow_scoring": {"previous": {"status": "passed"}},
        },
        {
            "champion_bundle_id": "sha256:secret-champion",
            "previous_bundle_id": "sha256:secret-previous",
        },
        {
            "status": "insufficient_data",
            "common_rows": 12,
            "minimum_common_rows": 30,
            "rolled_back": False,
        },
        generated_at="2026-08-29T00:00:00Z",
    )

    assert snapshot["status"] == "degraded"
    assert snapshot["schema"] == "quantiv.control-plane.v2"
    assert snapshot["publication_eligible"] is True
    assert snapshot["data"]["quarantine_records"] == 42
    assert snapshot["model"]["fallback_bundle_available"] is True
    assert snapshot["model"]["outcome_common_rows"] == 12
    assert snapshot["model"]["outcome_minimum_rows"] == 30
    assert "sample" not in snapshot["exceptions"][0]
    assert "secret-champion" not in str(snapshot)


def test_missing_manifests_are_explicitly_unavailable() -> None:
    snapshot = build_snapshot({}, {}, {}, {}, generated_at="now")

    assert snapshot["status"] == "unavailable"
    assert snapshot["publication_eligible"] is False
    assert snapshot["data"]["status"] == "unavailable"
    assert snapshot["model"]["status"] == "unavailable"


def test_feature_drift_degrades_model_health_without_blocking_publication() -> None:
    snapshot = build_snapshot(
        {"quality": {"status": "passed", "decision_safe": True}},
        {
            "status": "passed",
            "feature_drift": {"status": "warning", "critical_features": 3},
        },
        {"champion_bundle_id": "champion"},
        {},
        generated_at="now",
    )

    assert snapshot["status"] == "degraded"
    assert snapshot["publication_eligible"] is True
    assert snapshot["model"]["status"] == "degraded"


def test_critical_drift_blocks_publication() -> None:
    snapshot = build_snapshot(
        {"quality": {"status": "passed", "decision_safe": True}},
        {
            "status": "passed",
            "feature_drift": {"status": "critical", "critical_features": 1},
        },
        {"champion_bundle_id": "champion"},
        {},
        generated_at="now",
    )

    assert snapshot["status"] == "failed"
    assert snapshot["publication_eligible"] is False
    assert snapshot["model"]["status"] == "failed"


def test_missing_model_monitoring_blocks_publication_claim() -> None:
    snapshot = build_snapshot(
        {"quality": {"status": "passed", "decision_safe": True}},
        {},
        {"champion_bundle_id": "champion"},
        {},
        generated_at="now",
    )

    assert snapshot["publication_eligible"] is False
    assert snapshot["model"]["status"] == "unavailable"


def test_history_is_bounded_deduplicated_and_hash_free() -> None:
    older = build_snapshot(
        {
            "quality": {"status": "passed", "decision_safe": True},
            "source_reconciliation": {"source_date": "2026-08-28"},
            "event_coverage": {
                "coverage_pct": 0.75,
                "expected_events": 20,
                "covered_events": 15,
                "missing_events": 5,
            },
        },
        {"status": "passed", "feature_drift": {"status": "passed"}},
        {"champion_bundle_id": "sha256:secret-old"},
        {},
        generated_at="2026-08-29T12:00:00Z",
    )
    current = build_snapshot(
        {
            "quality": {"status": "degraded", "decision_safe": True},
            "source_reconciliation": {"source_date": "2026-08-29"},
            "event_coverage": {
                "coverage_pct": 0.80,
                "expected_events": 20,
                "covered_events": 16,
                "missing_events": 4,
            },
            "exceptions": [
                {"code": "missing_chain", "severity": "warning", "count": 4}
            ],
        },
        {
            "status": "passed",
            "feature_drift": {"status": "warning", "critical_features": 2},
        },
        {"champion_bundle_id": "sha256:secret-current"},
        {},
        generated_at="2026-08-30T12:00:00Z",
    )
    workflow = {
        "run_id": "42",
        "run_number": "183",
        "run_attempt": "1",
        "url": "https://github.com/example/quantiv/actions/runs/42",
    }
    history = update_history({}, older, limit=2)
    history = update_history(history, current, workflow=workflow, limit=2)
    history = update_history(history, current, workflow=workflow, limit=2)

    assert history["schema"] == "quantiv.control-plane-history.v1"
    assert len(history["runs"]) == 2
    assert history["runs"][0]["source_date"] == "2026-08-29"
    assert history["runs"][0]["warning_exceptions"] == 1
    assert history["runs"][1]["source_date"] == "2026-08-28"
    assert "secret-current" not in str(history)
    assert "secret-old" not in str(history)


def test_workflow_reference_is_actionable_without_commit_or_artifact_hashes() -> None:
    reference = build_workflow_reference(
        {
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_NUMBER": "183",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/quantiv",
        }
    )

    assert reference == {
        "run_id": "42",
        "run_number": "183",
        "run_attempt": "2",
        "url": "https://github.com/example/quantiv/actions/runs/42",
    }
