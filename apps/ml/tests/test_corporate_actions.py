from __future__ import annotations

import json
from datetime import date

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ml.corporate_actions import adjusted_post_price_sql
from scripts import sync_dolthub


def test_adjusted_post_price_neutralizes_split_and_dividend() -> None:
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE v_splits "
        "(act_symbol VARCHAR, ex_date DATE, to_factor DOUBLE, for_factor DOUBLE)"
    )
    conn.execute(
        "CREATE TABLE v_dividends "
        "(act_symbol VARCHAR, ex_date DATE, amount DOUBLE)"
    )
    conn.execute("INSERT INTO v_splits VALUES ('ACME', '2026-08-21', 2, 1)")
    conn.execute("INSERT INTO v_dividends VALUES ('ACME', '2026-08-22', 1)")
    expression = adjusted_post_price_sql(
        symbol="'ACME'",
        pre_date="DATE '2026-08-20'",
        post_date="DATE '2026-08-23'",
        post_price="52.0",
    )

    adjusted, realized = conn.execute(
        f"SELECT {expression}, ABS({expression} / 100.0 - 1.0)"
    ).fetchone()

    assert adjusted == 106.0
    assert round(realized, 8) == 0.06


def test_sync_corporate_actions_writes_replay_verified_active_universe_receipt(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync_dolthub, "API_DELAY", 0)
    snapshot = date(2026, 8, 21)
    partition = sync_dolthub._partition_path(snapshot, sync_dolthub.parquet_root())
    partition.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"act_symbol": ["AAPL", "BK", "AMWD"]}), partition
    )
    sync_dolthub.metadata_path().write_text(
        json.dumps({"last_sync_date": snapshot.isoformat()})
    )

    def fake_query(sql: str, api_url: str = "", retries: int = 3) -> list[dict]:
        assert api_url == sync_dolthub.STOCKS_API
        assert "'AAPL'" in sql and "'BNY'" in sql and "'AMWD'" not in sql
        if "FROM split" in sql:
            return [
                {
                    "act_symbol": "AAPL",
                    "ex_date": "2025-01-02",
                    "to_factor": 2,
                    "for_factor": 1,
                }
            ]
        if "FROM dividend" in sql:
            return [
                {
                    "act_symbol": "AAPL",
                    "ex_date": "2025-02-03",
                    "amount": 0.25,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(sync_dolthub, "query", fake_query)
    receipt = sync_dolthub.sync_corporate_actions("2019-01-01", "2026-08-23")

    assert receipt["source_options_date"] == snapshot.isoformat()
    assert receipt["universe"]["symbols"] == 2
    assert receipt["replay_equivalence"] == "verified"
    assert receipt["datasets"]["splits"]["rows"] == 1
    assert receipt["datasets"]["dividends"]["rows"] == 1
    assert receipt["revision"] == 1
    assert len(receipt["receipt_id"]) == 64
    assert sync_dolthub.corporate_action_manifest_path(snapshot).exists()
    assert sync_dolthub.corporate_action_latest_path().exists()
    assert sync_dolthub.corporate_action_receipt_path(
        snapshot, receipt["receipt_id"]
    ).exists()
    assert pq.read_table(
        tmp_path / receipt["datasets"]["splits"]["partition"]
    ).num_rows == 1


def test_sync_corporate_actions_versions_provider_revisions_for_same_options_date(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync_dolthub, "API_DELAY", 0)
    snapshot = date(2026, 8, 28)
    partition = sync_dolthub._partition_path(snapshot, sync_dolthub.parquet_root())
    partition.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"act_symbol": ["AAPL"]}), partition)
    sync_dolthub.metadata_path().write_text(
        json.dumps({"last_sync_date": snapshot.isoformat()})
    )
    provider_revision = {"value": 1}

    def fake_query(sql: str, api_url: str = "", retries: int = 3) -> list[dict]:
        assert api_url == sync_dolthub.STOCKS_API
        if "FROM split" in sql:
            rows = [
                {
                    "act_symbol": "AAPL",
                    "ex_date": "2025-01-02",
                    "to_factor": 2,
                    "for_factor": 1,
                }
            ]
            if provider_revision["value"] == 2:
                rows.append(
                    {
                        "act_symbol": "AAPL",
                        "ex_date": "2026-08-29",
                        "to_factor": 3,
                        "for_factor": 2,
                    }
                )
            return rows
        if "FROM dividend" in sql:
            return [
                {
                    "act_symbol": "AAPL",
                    "ex_date": "2025-02-03",
                    "amount": 0.25,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(sync_dolthub, "query", fake_query)
    first = sync_dolthub.sync_corporate_actions("2019-01-01", "2026-08-29")
    dated_path = sync_dolthub.corporate_action_manifest_path(snapshot)
    dated_before = dated_path.read_bytes()

    provider_revision["value"] = 2
    second = sync_dolthub.sync_corporate_actions("2019-01-01", "2026-08-30")

    assert second["revision"] == 2
    assert second["receipt_id"] != first["receipt_id"]
    assert second["predecessor_receipt_id"] == first["receipt_id"]
    assert second["change_summary"]["datasets"]["splits"] == {
        "previous_rows": 1,
        "current_rows": 2,
        "row_delta": 1,
        "content_changed": True,
    }
    assert dated_path.read_bytes() == dated_before
    assert json.loads(sync_dolthub.corporate_action_latest_path().read_text()) == second
    assert sync_dolthub.corporate_action_receipt_path(
        snapshot, first["receipt_id"]
    ).exists()
    assert sync_dolthub.corporate_action_receipt_path(
        snapshot, second["receipt_id"]
    ).exists()
