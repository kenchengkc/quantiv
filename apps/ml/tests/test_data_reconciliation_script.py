from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "build_data_reconciliation.py"


def _write_fresh_database(path: Path) -> None:
    today = date.today()
    snapshot = today - timedelta(days=1)
    earnings = today + timedelta(days=3)
    expiration = earnings + timedelta(days=1)
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE v_options (
                date DATE,
                act_symbol VARCHAR,
                expiration DATE,
                strike DOUBLE,
                call_put VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO v_options VALUES (?, 'ACME', ?, 100, ?)",
            [
                (snapshot, expiration, "Call"),
                (snapshot, expiration, "Put"),
            ],
        )
        conn.execute("CREATE TABLE v_ohlcv (date DATE, act_symbol VARCHAR)")
        conn.execute("INSERT INTO v_ohlcv VALUES (?, 'ACME')", [snapshot])
        conn.execute(
            "CREATE TABLE v_earnings (date DATE, act_symbol VARCHAR, timing VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO v_earnings VALUES (?, ?, 'amc')",
            [(earnings, "ACME"), (earnings, "MISS")],
        )
        conn.execute(
            """
            CREATE TABLE v_straddle_features (
                date DATE,
                act_symbol VARCHAR,
                expiration DATE
            )
            """
        )
        conn.execute(
            "INSERT INTO v_straddle_features VALUES (?, 'ACME', ?)",
            [snapshot, expiration],
        )
    finally:
        conn.close()


def _run(tmp_path: Path, db_path: Path) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "reconciliation.json"
    env = {**os.environ, "DATA_DIR": str(tmp_path / "data")}
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--duckdb-path",
            str(db_path),
            "--report",
            str(report_path),
            "--strict",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_script_reconciles_expected_and_received_events(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.duckdb"
    _write_fresh_database(db_path)

    result = _run(tmp_path, db_path)
    report = json.loads((tmp_path / "reconciliation.json").read_text())

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["quality"]["decision_safe"] is True
    assert report["quality"]["status"] == "degraded"
    assert report["event_coverage"] == {
        "window_days": 21,
        "expected_events": 2,
        "covered_events": 1,
        "missing_events": 1,
        "coverage_pct": 0.5,
        "missing_sample": [
            {
                "symbol": "MISS",
                "earnings_date": (date.today() + timedelta(days=3)).isoformat(),
            }
        ],
    }
    assert report["duplicates"]["options"]["duplicate_rows"] == 0


def test_script_writes_a_fail_closed_manifest_when_database_is_missing(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, tmp_path / "missing.duckdb")
    report = json.loads((tmp_path / "reconciliation.json").read_text())

    assert result.returncode == 1
    assert report["quality"]["decision_safe"] is False
    assert report["quality"]["critical_exceptions"] == 1
    assert report["exceptions"][0]["code"] == "duckdb_unavailable"
