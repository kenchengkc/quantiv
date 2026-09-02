from __future__ import annotations

import csv
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_earnings_calendar_integrity.py"

FIELDS = ["act_symbol", "date", "timing", "fiscal_year", "fiscal_q", "source"]


def _row(sym: str, dt: date, source: str) -> dict[str, str]:
    return {
        "act_symbol": sym,
        "date": dt.isoformat(),
        "timing": "unknown",
        "fiscal_year": str(dt.year),
        "fiscal_q": "Q1",
        "source": source,
    }


def _write_calendar(repo: Path, rows: list[dict[str, str]]) -> None:
    path = repo / "data" / "earnings_calendar.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_head_calendar(repo: Path, rows: list[dict[str, str]]) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _write_calendar(repo, rows)
    _git(repo, "add", "data/earnings_calendar.csv")
    _git(repo, "commit", "-m", "seed calendar")


def _run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def test_provider_only_future_event_churn_does_not_trip_gate(tmp_path):
    event_date = date.today() + timedelta(days=20)
    churn_rows = [_row(f"ZX{i:03d}", event_date, "finnhub") for i in range(170)]
    keep_row = _row("KEEP", event_date, "dolthub")
    _init_head_calendar(tmp_path, [keep_row, *churn_rows])

    _write_calendar(tmp_path, [keep_row])

    result = _run_gate(tmp_path, "--max-row-drop-pct", "100")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Provider-only event churn excluded from event gates" in result.stdout
    assert "170 next-60d" in result.stdout


def test_dolthub_backed_future_event_drop_still_trips_gate(tmp_path):
    event_date = date.today() + timedelta(days=20)
    vanished_rows = [_row(f"ZY{i:03d}", event_date, "dolthub") for i in range(170)]
    keep_row = _row("KEEP", event_date, "dolthub")
    _init_head_calendar(tmp_path, [keep_row, *vanished_rows])

    _write_calendar(tmp_path, [keep_row])

    result = _run_gate(tmp_path, "--max-row-drop-pct", "100", "--max-ticker-drop", "9999")

    assert result.returncode == 1
    assert "events in the next 60 days vanished vs HEAD" in result.stdout
