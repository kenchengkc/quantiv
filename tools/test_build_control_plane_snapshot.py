from build_control_plane_snapshot import (
    _read_verified_outcomes,
    build_release_status,
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
                "decision_group_rejection_rate": 0.04,
                "decision_groups": 50,
                "eligible_decision_groups": 48,
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
        {"evaluations": [{"evaluated_at": "2026-08-29T00:00:00Z"}]},
        generated_at="2026-08-29T00:00:00Z",
        release={
            "artifact_promotion_status": "passed",
            "data_r2_status": "passed",
            "forecast_r2_status": "passed",
            "frontend_payload_status": "passed",
            "neon_import_status": "failed",
        },
    )

    assert snapshot["status"] == "degraded"
    assert snapshot["schema"] == "quantiv.control-plane.v2"
    assert snapshot["publication_eligible"] is True
    assert snapshot["data"]["quarantine_records"] == 42
    assert snapshot["data"]["decision_group_rejection_rate"] == 0.04
    assert snapshot["data"]["eligible_decision_groups"] == 48
    assert snapshot["model"]["fallback_bundle_available"] is True
    assert snapshot["model"]["outcome_common_rows"] == 12
    assert snapshot["model"]["outcome_minimum_rows"] == 30
    assert snapshot["model"]["outcome_evaluations"] == 1
    assert snapshot["release"]["artifact_promotion_status"] == "passed"
    assert snapshot["release"]["neon_import_status"] == "failed"
    assert "sample" not in snapshot["exceptions"][0]
    assert "secret-champion" not in str(snapshot)


def test_unsigned_outcome_report_is_not_exposed(tmp_path) -> None:
    report = tmp_path / "latest_outcomes.json"
    report.write_text('{"status":"passed","common_rows":100}')

    outcomes, history = _read_verified_outcomes(
        report,
        tmp_path / "outcome_history.json",
        tmp_path / "latest_outcomes.receipt.json",
    )

    assert outcomes == {"status": "unverified"}
    assert history == {}


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
    assert history["runs"][0]["artifact_promotion_status"] == "unavailable"
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
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "REFRESH_STARTED_AT": "2026-08-30T11:00:00Z",
        },
        completed_at="2026-08-30T11:18:30Z",
    )

    assert reference == {
        "run_id": "42",
        "run_number": "183",
        "run_attempt": "2",
        "event_name": "workflow_dispatch",
        "started_at": "2026-08-30T11:00:00Z",
        "control_ready_seconds": 1110,
        "url": "https://github.com/example/quantiv/actions/runs/42",
    }


def test_release_status_normalizes_github_step_outcomes() -> None:
    release = build_release_status(
        {
            "CONTROL_DATA_R2_STATUS": "passed",
            "CONTROL_FORECAST_R2_STATUS": "success",
            "CONTROL_FRONTEND_PAYLOAD_STATUS": "completed",
            "CONTROL_NEON_IMPORT_STATUS": "failure",
        }
    )

    assert release == {
        "artifact_promotion_status": "passed",
        "data_r2_status": "passed",
        "forecast_r2_status": "passed",
        "frontend_payload_status": "passed",
        "neon_import_status": "failed",
    }
