from pathlib import Path

import pytest
import yaml

from scripts.frontend_publication_gate import evaluate_publication
from scripts.production_smoke_gate import evaluate_smoke


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



def test_smoke_runs_after_successful_frontend_publish_job():
    result = evaluate_smoke(
        event_name="workflow_run",
        workflow_run={
            "id": 456,
            "name": "Frontend publication release",
            "conclusion": "success",
            "head_branch": "main",
        },
        jobs=[{"name": "publish", "conclusion": "success"}],
    )
    assert result["should_smoke"] is True


def test_smoke_skips_after_frontend_publish_job_is_skipped():
    result = evaluate_smoke(
        event_name="workflow_run",
        workflow_run={
            "id": 456,
            "name": "Frontend publication release",
            "conclusion": "success",
            "head_branch": "main",
        },
        jobs=[
            {"name": "gate", "conclusion": "success"},
            {"name": "publish", "conclusion": "skipped"},
        ],
    )
    assert result["should_smoke"] is False
    assert "skipped" in result["reason"]


def test_direct_push_and_manual_smoke_still_run():
    assert evaluate_smoke(
        event_name="push",
        workflow_run=None,
    )["should_smoke"] is True
    assert evaluate_smoke(
        event_name="workflow_dispatch",
        workflow_run=None,
    )["should_smoke"] is True


def test_failed_frontend_publication_does_not_smoke():
    result = evaluate_smoke(
        event_name="workflow_run",
        workflow_run={
            "id": 456,
            "name": "Frontend publication release",
            "conclusion": "failure",
            "head_branch": "main",
        },
        jobs=[{"name": "publish", "conclusion": "success"}],
    )
    assert result["should_smoke"] is False


def test_production_smoke_workflow_uses_publish_job_gate():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/production-smoke.yml").read_text()
    )
    gate = workflow["jobs"]["gate"]
    smoke = workflow["jobs"]["smoke"]

    assert workflow["permissions"]["actions"] == "read"
    assert workflow["permissions"]["contents"] == "read"
    assert "production_smoke_gate.py" in gate["steps"][1]["run"]
    assert smoke["needs"] == "gate"
    assert smoke["if"] == "needs.gate.outputs.should_smoke == 'true'"
