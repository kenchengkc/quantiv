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


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_calendar(repo: Path, rows: list[dict[str, str]]) -> None:
    _write_rows(repo / "data" / "earnings_calendar.csv", rows)


def _write_baseline(repo: Path, rows: list[dict[str, str]]) -> None:
    _write_rows(
        repo / "data" / "validation" / "earnings_calendar_baseline.csv",
        rows,
    )


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
    _write_baseline(tmp_path, [keep_row, *churn_rows])
    _write_calendar(tmp_path, [keep_row])

    result = _run_gate(tmp_path, "--require-baseline", "--max-row-drop-pct", "100")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Provider-only event churn excluded from event gates" in result.stdout
    assert "170 next-60d" in result.stdout
    assert "earnings_calendar_baseline.csv" in result.stdout


def test_dolthub_backed_future_event_drop_still_trips_gate(tmp_path):
    event_date = date.today() + timedelta(days=20)
    vanished_rows = [_row(f"ZY{i:03d}", event_date, "dolthub") for i in range(170)]
    keep_row = _row("KEEP", event_date, "dolthub")
    _write_baseline(tmp_path, [keep_row, *vanished_rows])
    _write_calendar(tmp_path, [keep_row])

    result = _run_gate(
        tmp_path,
        "--require-baseline",
        "--max-row-drop-pct",
        "100",
        "--max-ticker-drop",
        "9999",
    )

    assert result.returncode == 1
    assert "events in the next 60 days vanished vs baseline" in result.stdout


def test_required_baseline_fails_closed(tmp_path):
    _write_calendar(tmp_path, [_row("KEEP", date.today(), "dolthub")])

    result = _run_gate(tmp_path, "--require-baseline")

    assert result.returncode == 1
    assert "Required prior-release baseline missing" in result.stdout
