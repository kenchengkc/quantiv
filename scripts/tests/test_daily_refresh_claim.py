from scripts.daily_refresh_claim import evaluate_claim


def _run(
    run_id: int,
    created_at: str,
    *,
    event: str = "workflow_dispatch",
    title: str = "Daily data refresh [normal]",
    attempt: int = 1,
):
    return {
        "id": run_id,
        "created_at": created_at,
        "event": event,
        "display_title": title,
        "run_attempt": attempt,
    }


def test_earliest_normal_run_owns_eastern_day():
    external = _run(100, "2026-09-28T06:00:02Z")
    delayed_schedule = _run(
        200,
        "2026-09-28T12:49:28Z",
        event="schedule",
        title="Daily data refresh [schedule]",
    )

    result = evaluate_claim(
        current_run=external,
        workflow_runs=[delayed_schedule, external],
    )
    assert result["claimed"] is True
    assert result["claim_date"] == "2026-09-28"
    assert result["owner_run_id"] == 100


def test_delayed_github_schedule_cannot_repeat_external_refresh():
    external = _run(100, "2026-09-28T06:00:02Z")
    delayed_schedule = _run(
        200,
        "2026-09-28T12:49:28Z",
        event="schedule",
        title="Daily data refresh [schedule]",
    )

    result = evaluate_claim(
        current_run=delayed_schedule,
        workflow_runs=[external, delayed_schedule],
    )
    assert result["claimed"] is False
    assert result["owner_run_id"] == 100


def test_provider_free_recovery_never_blocks_normal_claim():
    recovery = _run(
        50,
        "2026-09-28T05:30:00Z",
        title="Daily data refresh [provider-free-recovery]",
    )
    normal = _run(100, "2026-09-28T06:00:02Z")

    result = evaluate_claim(current_run=normal, workflow_runs=[recovery, normal])
    assert result["claimed"] is True
    assert result["owner_run_id"] == 100


def test_normal_rerun_attempt_cannot_reissue_provider_refresh():
    normal = _run(100, "2026-09-28T06:00:02Z", attempt=2)
    result = evaluate_claim(current_run=normal, workflow_runs=[normal])
    assert result["claimed"] is False
    assert "provider-free recovery" in result["reason"]


def test_claim_date_uses_eastern_calendar_day():
    run = _run(100, "2026-09-28T03:30:00Z")
    result = evaluate_claim(current_run=run, workflow_runs=[run])
    assert result["claim_date"] == "2026-09-27"
