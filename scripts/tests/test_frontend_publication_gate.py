from pathlib import Path

import pytest
import yaml

from scripts.frontend_publication_gate import evaluate_publication


ROOT = Path(__file__).resolve().parents[2]


def _run(
    *,
    name="Daily data refresh [normal]",
    conclusion="success",
    branch="main",
    event="workflow_dispatch",
):
    return {
        "id": 123,
        "name": name,
        "conclusion": conclusion,
        "head_branch": branch,
        "event": event,
    }


def test_manual_frontend_publication_is_allowed():
    result = evaluate_publication(
        event_name="workflow_dispatch",
        workflow_run=None,
    )
    assert result["should_publish"] is True


def test_successful_normal_refresh_publishes():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(),
        jobs=[
            {"name": "refresh", "conclusion": "success"},
            {"name": "recovery", "conclusion": "skipped"},
        ],
    )
    assert result["should_publish"] is True
    assert "refresh" in result["reason"]


def test_successful_provider_free_recovery_publishes():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(name="Daily data refresh [provider-free-recovery]"),
        jobs=[
            {"name": "refresh", "conclusion": "skipped"},
            {"name": "recovery", "conclusion": "success"},
        ],
    )
    assert result["should_publish"] is True
    assert "recovery" in result["reason"]


def test_duplicate_scheduled_refresh_with_skipped_producers_does_not_publish():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(
            name="Daily data refresh [schedule]",
            event="schedule",
        ),
        jobs=[
            {"name": "refresh", "conclusion": "skipped"},
            {"name": "recovery", "conclusion": "skipped"},
        ],
    )
    assert result["should_publish"] is False
    assert "refresh=skipped" in result["reason"]
    assert "recovery=skipped" in result["reason"]


def test_failed_daily_refresh_does_not_publish_even_if_job_data_is_present():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(conclusion="failure"),
        jobs=[{"name": "refresh", "conclusion": "success"}],
    )
    assert result["should_publish"] is False


def test_non_main_producer_does_not_publish():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(branch="feature"),
        jobs=[{"name": "refresh", "conclusion": "success"}],
    )
    assert result["should_publish"] is False


def test_ci_pull_request_completion_does_not_publish():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(name="CI", event="pull_request"),
    )
    assert result["should_publish"] is False


def test_ci_push_completion_still_publishes():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(name="CI", event="push"),
    )
    assert result["should_publish"] is True


def test_other_successful_main_producer_still_publishes():
    result = evaluate_publication(
        event_name="workflow_run",
        workflow_run=_run(name="Refresh ticker names", event="workflow_dispatch"),
    )
    assert result["should_publish"] is True


def test_daily_refresh_requires_job_results():
    with pytest.raises(ValueError, match="requires producer job results"):
        evaluate_publication(
            event_name="workflow_run",
            workflow_run=_run(),
            jobs=None,
        )


def test_frontend_publication_workflow_uses_gate_job():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/frontend-publication.yml").read_text()
    )
    gate = workflow["jobs"]["gate"]
    publish = workflow["jobs"]["publish"]

    assert workflow["permissions"]["actions"] == "read"
    assert workflow["permissions"]["contents"] == "read"
    assert "frontend_publication_gate.py" in gate["steps"][1]["run"]
    assert publish["needs"] == "gate"
    assert publish["if"] == "needs.gate.outputs.should_publish == 'true'"
    assert publish["permissions"]["contents"] == "write"
