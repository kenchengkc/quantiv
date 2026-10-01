import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("bucket,expected", [
    (None, "r2:quantiv-data/validation/data_reconciliation.json"),
    ("", "r2:quantiv-data/validation/data_reconciliation.json"),
    ("custom-bucket", "r2:custom-bucket/validation/data_reconciliation.json"),
])
def test_recovery_evidence_uses_the_configured_or_default_bucket(tmp_path, bucket, expected):
    workflow = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    step = next(step for step in workflow["jobs"]["recovery"]["steps"]
                if step["name"] == "Restore saved reconciliation and calendar evidence")
    # Inspect the first real shell-to-rclone boundary, then stop before any
    # downloads or verification commands can run.
    probe = tmp_path / "rclone"
    probe.write_text('#!/bin/sh\nprintf "%s" "$2" > "$CALL_LOG"\nexit 42\n')
    probe.chmod(0o755)
    log = tmp_path / "requested-source"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "CALL_LOG": str(log)}
    env.pop("R2_BUCKET", None)
    if bucket is not None:
        env["R2_BUCKET"] = bucket
    result = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 42, result.stderr
    assert log.read_text() == expected


def test_recovery_job_has_no_market_data_provider_steps() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    recovery = workflow["jobs"]["recovery"]

    assert recovery["env"]["PROVIDER_FREE_RECOVERY"] == "1"
    assert "--run-shell {0}" not in str(recovery.get("defaults", {}))

    commands = "\n".join(
        str(step.get("run", ""))
        for step in recovery["steps"]
    )
    forbidden = (
        "sync_dolthub.py",
        "sync_fmp_earnings.py",
        "sync_finnhub_earnings.py",
        "sync_finnhub_market_holidays.py",
        "sync_vix.py",
        "reconcile_earnings_calendar.py",
        "pull_market_caps.py",
        "sync_provider_enrichments.py",
    )
    for command in forbidden:
        assert command not in commands

    provider_keys = {
        "FINNHUB_API_KEY",
        "FMP_API_KEY",
        "ALPHAVANTAGE_API_KEY",
        "TWELVEDATA_API_KEY",
        "POLYGON_API_KEY",
        "MASSIVE_API_KEY",
        "ALPACA_API_KEY",
    }
    for step in recovery["steps"]:
        assert provider_keys.isdisjoint(step.get("env", {}).keys())

    assert "scripts/verify_refresh_recovery.py" in commands
    assert "--expected-release-id" in commands
    assert "--expected-manifest-id" in commands
    assert "scripts/materialize_frontend_release.sh" in commands
    assert "FRONTEND_RELEASE_REQUIRED" in str(recovery["steps"])
    assert "scripts/run_provider_free.sh" in commands
    assert "tools/build_research_history.py --preserve-existing" in commands
    assert "apps/frontend/scripts/build-research-history.mjs" in commands
    assert "ACTIVE_OPTIONS_DATE" in commands
    assert "quantiv.historical-event-universe.v1" in commands


def test_normal_refresh_keeps_hard_provider_deadline() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    refresh = workflow["jobs"]["refresh"]
    assert "--run-shell {0}" in refresh["defaults"]["run"]["shell"]
    admit = next(
        step
        for step in refresh["steps"]
        if step["name"] == "Gate — reserve market-hours API capacity"
    )
    assert "provider_market_hours.py --admit" in admit["run"]


def test_daily_claim_is_serialized_before_refresh_execution() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    claim = workflow["jobs"]["claim"]
    refresh = workflow["jobs"]["refresh"]
    recovery = workflow["jobs"]["recovery"]

    assert claim["concurrency"]["group"] == "daily-data-refresh-claim"
    assert claim["concurrency"]["cancel-in-progress"] is False
    assert refresh["needs"] == "claim"
    assert refresh["concurrency"]["group"] == "daily-data-refresh-execution"
    assert recovery["concurrency"]["group"] == "daily-data-refresh-execution"


def test_native_schedule_is_backup_off_the_top_of_hour() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    triggers = workflow.get("on") or workflow.get(True)
    schedules = triggers["schedule"]
    assert schedules == [
        {"cron": "17 2 * * *", "timezone": "America/New_York"}
    ]


def test_provider_free_wrapper_strips_market_data_credentials(tmp_path) -> None:
    script = ROOT / "scripts/run_provider_free.sh"
    probe = (
        "import json, os; "
        "print(json.dumps({"
        "'mode': os.getenv('PROVIDER_FREE_RECOVERY'), "
        "'finnhub': os.getenv('FINNHUB_API_KEY'), "
        "'fmp': os.getenv('FMP_API_KEY'), "
        "'twelve': os.getenv('TWELVEDATA_API_KEY'), "
        "'polygon': os.getenv('POLYGON_API_KEY'), "
        "'alpaca': os.getenv('ALPACA_API_KEY'), "
        "'database': os.getenv('DATABASE_URL')"
        "}))"
    )
    env = {
        **os.environ,
        "FINNHUB_API_KEY": "secret",
        "FMP_API_KEY": "secret",
        "TWELVEDATA_API_KEY": "secret",
        "POLYGON_API_KEY": "secret",
        "ALPACA_API_KEY": "secret",
        "DATABASE_URL": "postgres://allowed-non-market-service",
    }
    result = subprocess.run(
        ["bash", str(script), "python", "-c", probe],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "mode": "1",
        "finnhub": None,
        "fmp": None,
        "twelve": None,
        "polygon": None,
        "alpaca": None,
        "database": "postgres://allowed-non-market-service",
    }
