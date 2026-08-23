from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


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
                call_put VARCHAR,
                bid DOUBLE,
                ask DOUBLE,
                mid DOUBLE,
                relative_spread DOUBLE,
                iv DOUBLE,
                delta DOUBLE,
                option_volume BIGINT,
                open_interest BIGINT,
                source_quote_timestamp TIMESTAMP,
                quote_timestamp_precision VARCHAR,
                market_data_mode VARCHAR,
                quote_quality_status VARCHAR,
                quote_rejection_reason VARCHAR
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO v_options VALUES (
                ?, ?, ?, 100, ?, 1.0, 1.1, 1.05, 0.095, 0.5, ?,
                NULL, NULL, NULL, 'date', 'end_of_day', 'eligible', NULL
            )
            """,
            [
                (snapshot, "ACME", expiration, "Call", 0.5),
                (snapshot, "ACME", expiration, "Put", -0.5),
                (snapshot, "MISS", expiration, "Call", 0.5),
                (snapshot, "MISS", expiration, "Put", -0.5),
            ],
        )
        conn.execute("CREATE TABLE v_options_raw AS SELECT * FROM v_options")
        conn.execute("CREATE TABLE v_ohlcv (date DATE, act_symbol VARCHAR)")
        conn.execute("INSERT INTO v_ohlcv VALUES (?, 'ACME')", [snapshot])
        conn.execute(
            "CREATE TABLE v_earnings (date DATE, act_symbol VARCHAR, timing VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO v_earnings VALUES (?, ?, 'amc')",
            [(earnings, "ACME"), (earnings, "MISS"), (earnings, "OUTSIDE")],
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
        conn.executemany(
            "INSERT INTO v_straddle_features VALUES (?, ?, ?)",
            [(snapshot, "ACME", expiration), (snapshot, "MISS", expiration)],
        )
        conn.execute(
            """
            CREATE TABLE v_straddle_candidates (
                date DATE, act_symbol VARCHAR, expiration DATE, dte INTEGER,
                strike DOUBLE, call_bid DOUBLE, call_ask DOUBLE, call_mid DOUBLE,
                call_relative_spread DOUBLE, call_iv DOUBLE, call_delta DOUBLE,
                call_gamma DOUBLE, call_vega DOUBLE, call_theta DOUBLE,
                call_volume BIGINT, call_open_interest BIGINT,
                call_quote_timestamp TIMESTAMP,
                put_bid DOUBLE, put_ask DOUBLE, put_mid DOUBLE,
                put_relative_spread DOUBLE, put_iv DOUBLE, put_delta DOUBLE,
                put_volume BIGINT, put_open_interest BIGINT,
                put_quote_timestamp TIMESTAMP,
                straddle_bid DOUBLE, straddle_ask DOUBLE, straddle_mid DOUBLE,
                straddle_relative_spread DOUBLE, atm_delta_distance DOUBLE,
                quote_timestamp_precision VARCHAR, market_data_mode VARCHAR,
                pair_rejection_reason VARCHAR, pair_quality_status VARCHAR,
                pair_rank INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO v_straddle_candidates VALUES (
                ?, ?, ?, 7, 100, 1, 1.1, 1.05, .095, .5, .5,
                .1, .1, -.1, NULL, NULL, NULL,
                1, 1.1, 1.05, .095, .5, -.5, NULL, NULL, NULL,
                2, 2.2, 2.1, .095, 0,
                'date', 'end_of_day', NULL, 'eligible', 1
            )
            """,
            [(snapshot, "ACME", expiration), (snapshot, "MISS", expiration)],
        )
        conn.execute(
            """
            CREATE VIEW v_option_quote_quarantine AS
            SELECT *, CAST(NULL AS VARCHAR) AS rejection_reason
            FROM v_options WHERE FALSE
            """
        )
        conn.execute(
            """
            CREATE VIEW v_straddle_quote_quarantine AS
            SELECT * FROM v_straddle_candidates WHERE FALSE
            """
        )
    finally:
        conn.close()


def _run(tmp_path: Path, db_path: Path) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "reconciliation.json"
    data_dir = tmp_path / "data"
    snapshot = date.today() - timedelta(days=1)
    partition = (
        data_dir / "parquet" / "options_chain" / f"year={snapshot.year}"
        / f"month={snapshot.month:02d}" / f"{snapshot}.parquet"
    )
    partition.parent.mkdir(parents=True, exist_ok=True)
    partition.write_bytes(b"reconciled-test-partition")
    partition_hash = hashlib.sha256(partition.read_bytes()).hexdigest()
    manifest_path = data_dir / "control" / "ingestion" / "options" / f"{snapshot}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "expected_rows": 4,
                "received_rows": 4,
                "partition": str(partition.relative_to(data_dir)),
                "partition_sha256": partition_hash,
                "content_sha256": "test-content",
                "replay_equivalence": "verified",
            }
        )
    )
    action_dir = data_dir / "parquet" / "corporate_actions"
    action_dir.mkdir(parents=True, exist_ok=True)
    empty_content_hash = hashlib.sha256(b"").hexdigest()
    split_path = action_dir / "splits" / f"{empty_content_hash}.parquet"
    dividend_path = action_dir / "dividends" / f"{empty_content_hash}.parquet"
    split_path.parent.mkdir(parents=True, exist_ok=True)
    dividend_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "act_symbol": pa.array([], type=pa.string()),
                "ex_date": pa.array([], type=pa.date32()),
                "to_factor": pa.array([], type=pa.float64()),
                "for_factor": pa.array([], type=pa.float64()),
            }
        ),
        split_path,
    )
    pq.write_table(
        pa.table(
            {
                "act_symbol": pa.array([], type=pa.string()),
                "ex_date": pa.array([], type=pa.date32()),
                "amount": pa.array([], type=pa.float64()),
            }
        ),
        dividend_path,
    )
    action_manifest_path = (
        data_dir
        / "control"
        / "ingestion"
        / "corporate_actions"
        / f"{snapshot}.json"
    )
    action_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    action_manifest_path.write_text(
        json.dumps(
            {
                "schema": "quantiv.corporate-action-ingestion.v1",
                "generated_at": f"{date.today()}T00:00:00Z",
                "source": "dolthub:post-no-preference/stocks",
                "source_options_date": snapshot.isoformat(),
                "query_start": "2019-01-01",
                "query_end": date.today().isoformat(),
                "universe": {
                    "symbols": 2,
                    "symbols_sha256": hashlib.sha256(b"ACME\nMISS").hexdigest(),
                    "method": "latest_options_partition_excluding_retired_symbols",
                },
                "datasets": {
                    "splits": {
                        "rows": 0,
                        "partition": str(split_path.relative_to(data_dir)),
                        "partition_sha256": hashlib.sha256(
                            split_path.read_bytes()
                        ).hexdigest(),
                        "content_sha256": hashlib.sha256(b"").hexdigest(),
                        "batches": [
                            {
                                "completion": "short_page",
                                "pages": 1,
                                "rows": 0,
                                "symbols": 2,
                            }
                        ],
                    },
                    "dividends": {
                        "rows": 0,
                        "partition": str(dividend_path.relative_to(data_dir)),
                        "partition_sha256": hashlib.sha256(
                            dividend_path.read_bytes()
                        ).hexdigest(),
                        "content_sha256": hashlib.sha256(b"").hexdigest(),
                        "batches": [
                            {
                                "completion": "short_page",
                                "pages": 1,
                                "rows": 0,
                                "symbols": 2,
                            }
                        ],
                    },
                },
                "replay_equivalence": "verified",
                "adjustment_contract": {
                    "split": "test",
                    "dividend": "test",
                    "scope": "test",
                },
            }
        )
    )
    env = {**os.environ, "DATA_DIR": str(data_dir)}
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


def test_script_reconciles_source_quotes_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.duckdb"
    _write_fresh_database(db_path)

    result = _run(tmp_path, db_path)
    report = json.loads((tmp_path / "reconciliation.json").read_text())

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["quality"]["decision_safe"] is True
    assert report["quality"]["status"] == "degraded"
    assert report["event_coverage"]["calendar_events"] == 3
    assert report["event_coverage"]["expected_events"] == 2
    assert report["event_coverage"]["covered_events"] == 2
    assert report["event_coverage"]["outside_option_universe_events"] == 1
    assert report["event_coverage"]["status"] == "passed"
    assert report["quote_quality"]["status"] == "passed"
    assert report["source_reconciliation"]["status"] == "passed"
    assert report["pipeline_controls"]["quarantine"]["status"] == "enforced"
    assert report["duplicates"]["options"]["duplicate_rows"] == 0


def test_script_writes_a_fail_closed_manifest_when_database_is_missing(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, tmp_path / "missing.duckdb")
    report = json.loads((tmp_path / "reconciliation.json").read_text())

    assert result.returncode == 1
    assert report["quality"]["decision_safe"] is False
    assert report["quality"]["critical_exceptions"] >= 1
    assert "duckdb_unavailable" in {issue["code"] for issue in report["exceptions"]}
