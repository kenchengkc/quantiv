from datetime import datetime
from pathlib import Path
import shlex

import pytest
import yaml

import provider_market_hours


@pytest.mark.parametrize("instant,budget", [
    ("2026-09-24T05:17:00+00:00", 180),
    ("2026-12-24T05:17:00+00:00", 180),
    ("2026-09-24T05:30:00-04:00", 180),
    ("2026-12-24T05:30:00-05:00", 180),
    ("2026-09-24T04:30:00-04:00", 240),
    # Six elapsed hours cross the spring-forward jump and finish at 08:30.
    ("2026-03-08T01:30:00-05:00", 360),
])
def test_full_job_budget_fits_before_morning_deadline(instant, budget):
    deadline = provider_market_hours.require_premarket_refresh_window(
        budget, now=datetime.fromisoformat(instant)
    )
    assert (deadline.hour, deadline.minute) == (8, 30)


@pytest.mark.parametrize("instant,budget", [
    ("2026-09-24T05:30:01-04:00", 180),
    ("2026-12-24T05:30:01-05:00", 180),
    ("2026-09-24T04:30:01-04:00", 240),
    ("2026-09-23T15:13:49+00:00", 180),  # This morning's delayed start.
    ("2026-09-23T18:09:20+00:00", 180),  # The manual recovery start.
    ("2026-09-24T09:24:00-04:00", 180),
    ("2026-09-24T17:00:00-04:00", 180),
    ("2026-09-24T23:00:00-04:00", 180),
    ("2026-09-26T10:00:00-04:00", 240),  # Weekend sweeps obey the same policy.
    ("2026-03-08T01:30:01-05:00", 360),
])
def test_late_jobs_are_blocked_even_when_soft_skip_is_enabled(monkeypatch, instant, budget):
    monkeypatch.setenv("FINNHUB_RESERVED_WINDOW_SOFT_SKIP", "1")
    with pytest.raises(SystemExit, match="08:30"):
        provider_market_hours.require_premarket_refresh_window(
            budget, now=datetime.fromisoformat(instant)
        )


@pytest.mark.parametrize("budget", [0, -1])
def test_invalid_job_budget_is_rejected(budget):
    with pytest.raises(ValueError):
        provider_market_hours.require_premarket_refresh_window(budget)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError):
        provider_market_hours.require_premarket_refresh_window(
            180, now=datetime(2026, 9, 24, 1)
        )


def test_each_refresh_job_guards_its_entire_timeout_before_provider_access():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/data-refresh.yml").read_text())
    for job in workflow["jobs"].values():
        commands = [(index, step["run"]) for index, step in enumerate(job["steps"]) if "run" in step]
        guards = [(index, shlex.split(command)) for index, command in commands
                  if "provider_market_hours.py" in command]
        assert len(guards) == 1, "Every provider job needs an admission guard"
        guard_index, args = guards[0]
        budget_index = args.index("--max-runtime-minutes") + 1
        assert int(args[budget_index]) == job["timeout-minutes"]
        for index, step in enumerate(job["steps"]):
            if any(key.endswith("API_KEY") for key in step.get("env", {})):
                assert guard_index <= index, "Guard must run before provider calls"
        assert all("--allow-market-hours" not in command for _, command in commands)
