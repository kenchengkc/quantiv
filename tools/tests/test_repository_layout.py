from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_tool_tests_are_centralized() -> None:
    assert not list((REPO_ROOT / "tools").glob("test_*.py"))


def test_live_application_code_has_no_archive_directories() -> None:
    archives = [path for path in (REPO_ROOT / "apps").rglob("archive") if path.is_dir()]
    assert archives == []


def test_specialized_playwright_configs_live_with_e2e() -> None:
    frontend = REPO_ROOT / "apps" / "frontend"
    assert not list(frontend.glob("playwright.*.config.ts"))
    assert (frontend / "e2e" / "config").is_dir()
    assert (frontend / "e2e" / "performance-budget.json").is_file()


def test_research_artifacts_are_namespaced() -> None:
    data = REPO_ROOT / "data"
    assert not (data / "alpha_vantage_voi_probe.json").exists()
    assert not (data / "event_signals_panel.jsonl").exists()
    assert not (data / "provider_enrichments_pm").exists()
    assert not (data / "provider_usage_ledger_pm.json").exists()
    assert (data / "research" / "provider_probes").is_dir()
    assert (data / "research" / "provider_signals" / "pm").is_dir()
    assert (data / "research" / "event_signals").is_dir()
