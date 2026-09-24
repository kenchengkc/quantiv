from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess
import time

import pytest
import yaml

import provider_market_hours


@pytest.mark.parametrize("instant", [
    "2026-09-24T02:00:00-04:00",
    "2026-12-24T02:00:00-05:00",
    "2026-09-24T08:59:59-04:00",
    "2026-12-24T08:59:59-05:00",
    "2026-03-08T03:00:00-04:00",
    "2026-11-01T03:00:00-05:00",
])
def test_starts_before_nine_are_admitted(instant):
    deadline = provider_market_hours.require_premarket_refresh_window(
        now=datetime.fromisoformat(instant)
    )
    assert (deadline.hour, deadline.minute) == (9, 35)


@pytest.mark.parametrize("instant", [
    "2026-09-24T09:00:00-04:00",
    "2026-12-24T09:00:00-05:00",
    "2026-09-23T15:13:49+00:00",
    "2026-09-23T18:09:20+00:00",
    "2026-09-24T17:00:00-04:00",
    "2026-09-26T10:00:00-04:00",
])
def test_late_jobs_are_rejected_even_with_soft_skip(monkeypatch, instant):
    monkeypatch.setenv("FINNHUB_RESERVED_WINDOW_SOFT_SKIP", "1")
    with pytest.raises(SystemExit, match="09:00"):
        provider_market_hours.require_premarket_refresh_window(
            now=datetime.fromisoformat(instant)
        )


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError):
        provider_market_hours.require_premarket_refresh_window(now=datetime(2026, 9, 24, 3))


def test_shell_preserves_command_exit_status(tmp_path):
    script = tmp_path / "step.sh"
    script.write_text("exit 7\n")
    assert provider_market_hours.run_refresh_shell(
        script, datetime.now(timezone.utc) + timedelta(seconds=10)
    ) == 7


def test_expired_deadline_blocks_provider_command(tmp_path):
    marker = tmp_path / "provider-called"
    script = tmp_path / "step.sh"
    script.write_text(f"touch '{marker}'\n")
    assert provider_market_hours.run_refresh_shell(
        script, datetime.now(timezone.utc) - timedelta(seconds=1)
    ) == 124
    assert not marker.exists()


@pytest.mark.parametrize("instant,expected_status", [
    ("2026-09-24T09:25:00-04:00", 0),
    ("2026-09-24T09:34:59-04:00", 0),
    ("2026-09-24T09:35:00-04:00", 124),
    ("2026-12-24T09:34:59-05:00", 0),
    ("2026-12-24T09:35:00-05:00", 124),
])
def test_admitted_refresh_can_finish_until_935(monkeypatch, tmp_path, instant, expected_status):
    clock = datetime.fromisoformat(instant)
    deadline = provider_market_hours.require_premarket_refresh_window(
        now=clock.replace(hour=2, minute=0, second=0)
    )

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz)

    monkeypatch.setattr(provider_market_hours, "datetime", FrozenClock)
    marker = tmp_path / "published"
    script = tmp_path / "step.sh"
    script.write_text(f"touch '{marker}'\n")
    assert provider_market_hours.run_refresh_shell(script, deadline) == expected_status
    assert marker.exists() == (expected_status == 0)


def test_deadline_terminates_provider_process_group(tmp_path):
    marker = tmp_path / "provider-called"
    script = tmp_path / "step.sh"
    script.write_text(f"(sleep 1; touch '{marker}') &\nwait\n")
    assert provider_market_hours.run_refresh_shell(
        script, datetime.now(timezone.utc) + timedelta(seconds=0.1)
    ) == 124
    time.sleep(1.1)
    assert not marker.exists()


def test_shell_requires_prior_admission(tmp_path):
    script = tmp_path / "step.sh"
    marker = tmp_path / "provider-called"
    script.write_text(f"touch '{marker}'\n")
    env = {k: v for k, v in os.environ.items() if k != "REFRESH_DEADLINE_UTC"}
    result = subprocess.run(
        ["python3", str(Path(provider_market_hours.__file__)), "--run-shell", str(script)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not marker.exists()


def test_admitted_shell_cli_preserves_actions_output_environment(tmp_path):
    script = tmp_path / "step.sh"
    output = tmp_path / "github-output"
    script.write_text('echo "result=published" >> "$GITHUB_OUTPUT"\n')
    result = subprocess.run(
        ["python3", str(Path(provider_market_hours.__file__)), "--run-shell", str(script)],
        env={
            **os.environ,
            "REFRESH_DEADLINE_UTC": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(),
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "result=published\n"


def test_workflow_deadline_covers_every_provider_step():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/data-refresh.yml").read_text())
    for job in workflow["jobs"].values():
        assert "--run-shell {0}" in job["defaults"]["run"]["shell"]
        admission = None
        for index, step in enumerate(job["steps"]):
            command = step.get("run", "")
            if "--admit" in command:
                admission = index
                assert step["shell"] == "bash"
            if any(key.endswith("API_KEY") for key in step.get("env", {})):
                assert admission is not None and admission < index
                assert "shell" not in step, "Provider steps must use the deadline wrapper"
            assert "--allow-market-hours" not in command
