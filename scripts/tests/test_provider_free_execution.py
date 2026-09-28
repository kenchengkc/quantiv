from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


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
