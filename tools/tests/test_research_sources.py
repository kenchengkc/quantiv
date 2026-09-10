from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb

from frontend_data.research_sources import (
    bind_retired_membership,
    fetch_retired_research_sources,
    install_research_corporate_actions,
    install_retired_earnings,
)


def _fake_query(sql: str, api_url: str) -> list[dict]:
    del api_url
    if "FROM earnings_calendar" in sql:
        return [
            {
                "act_symbol": "OLD",
                "date": "2025-04-10",
                "when": "Before market open",
            }
        ]
    if "FROM split" in sql:
        return [
            {
                "act_symbol": "OLD",
                "ex_date": "2025-04-11",
                "to_factor": 2,
                "for_factor": 1,
            }
        ]
    if "FROM dividend" in sql:
        return [
            {
                "act_symbol": "OLD",
                "ex_date": "2025-04-11",
                "amount": 0.25,
            }
        ]
    raise AssertionError(f"unexpected query: {sql}")


def test_retired_membership_fetch_is_bounded_deterministic_and_auditable() -> None:
    first = fetch_retired_research_sources(
        _fake_query,
        earnings_api="earnings-api",
        stocks_api="stocks-api",
        retired_tickers={"OLD", "NONE"},
        action_end=date(2026, 1, 1),
    )
    second = fetch_retired_research_sources(
        _fake_query,
        earnings_api="earnings-api",
        stocks_api="stocks-api",
        retired_tickers={"NONE", "OLD"},
        action_end=date(2026, 1, 1),
    )

    assert first == second
    assert first["status"] == "verified"
    assert first["configured_tickers"] == ["NONE", "OLD"]
    assert first["earnings"]["row_count"] == 1
    assert first["earnings"]["rows"][0]["timing"] == "before_market_open"
    assert first["corporate_actions"]["splits"]["row_count"] == 1
    assert first["corporate_actions"]["dividends"]["row_count"] == 1
    assert first["missing_earnings_tickers"] == ["NONE"]
    assert first["earnings"]["sha256"].startswith("sha256:")


def test_retired_earnings_are_research_only_and_do_not_overwrite_existing_rows() -> None:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE earnings_events (
            ticker VARCHAR,
            earnings_dt DATE,
            timing VARCHAR,
            source VARCHAR,
            confirmed_flag BOOLEAN,
            fiscal_year BIGINT,
            fiscal_q VARCHAR,
            eps_actual DOUBLE,
            eps_estimate DOUBLE,
            revenue_actual DOUBLE,
            revenue_estimate DOUBLE,
            timing_source VARCHAR
        )
        """
    )
    rows = [
        {"ticker": "OLD", "date": "2025-04-10", "timing": "before_market_open"}
    ]

    assert install_retired_earnings(conn, rows) == 1
    assert install_retired_earnings(conn, rows) == 0
    stored = conn.execute(
        "SELECT ticker, timing, source, timing_source FROM earnings_events"
    ).fetchone()
    assert stored == (
        "OLD",
        "before_market_open",
        "dolthub_retired_membership",
        "reported",
    )


def _write_action_fixture(root: Path) -> None:
    action_root = root / "parquet" / "corporate_actions"
    split_path = action_root / "splits" / "active.parquet"
    dividend_path = action_root / "dividends" / "active.parquet"
    split_path.parent.mkdir(parents=True)
    dividend_path.parent.mkdir(parents=True)

    writer = duckdb.connect()
    writer.execute(
        f"""
        COPY (
          SELECT 'LIVE'::VARCHAR AS act_symbol, DATE '2025-01-02' AS ex_date,
                 3.0::DOUBLE AS to_factor, 2.0::DOUBLE AS for_factor
        ) TO '{split_path}' (FORMAT PARQUET)
        """
    )
    writer.execute(
        f"""
        COPY (
          SELECT 'LIVE'::VARCHAR AS act_symbol, DATE '2025-01-03' AS ex_date,
                 0.10::DOUBLE AS amount
        ) TO '{dividend_path}' (FORMAT PARQUET)
        """
    )
    writer.close()

    receipt = root / "control" / "ingestion" / "corporate_actions" / "latest.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "receipt_id": "receipt-fixture",
                "source_options_date": "2026-01-01",
                "datasets": {
                    "splits": {
                        "partition": "parquet/corporate_actions/splits/active.parquet"
                    },
                    "dividends": {
                        "partition": "parquet/corporate_actions/dividends/active.parquet"
                    },
                },
            }
        )
    )


def test_research_corporate_actions_union_active_and_retired_sources(tmp_path: Path) -> None:
    _write_action_fixture(tmp_path)
    conn = duckdb.connect()

    evidence = install_research_corporate_actions(
        conn,
        data_dir=tmp_path,
        supplemental_splits=[
            {
                "ticker": "OLD",
                "ex_date": "2025-04-11",
                "to_factor": 2.0,
                "for_factor": 1.0,
            }
        ],
        supplemental_dividends=[
            {"ticker": "OLD", "ex_date": "2025-04-11", "amount": 0.25}
        ],
    )

    assert conn.execute("SELECT COUNT(*) FROM v_splits").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM v_dividends").fetchone()[0] == 2
    assert evidence["retired_split_rows"] == 1
    assert evidence["retired_dividend_rows"] == 1
    assert evidence["receipt_id"] == "receipt-fixture"


def test_membership_evidence_is_part_of_universe_identity() -> None:
    base = {
        "schema": "quantiv.historical-event-universe.v1",
        "source": {
            "kind": "analytical_duckdb",
            "completeness": "source_level",
            "as_of_date": "2026-01-01",
        },
        "evidence_rule": "fixture",
        "decision_scope": "end_of_day_research",
        "live_trading_eligible": False,
        "event_count": 0,
        "audit": {},
        "events": [],
        "universe_id": "sha256:" + "0" * 64,
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    source = fetch_retired_research_sources(
        _fake_query,
        earnings_api="earnings-api",
        stocks_api="stocks-api",
        retired_tickers={"OLD"},
        action_end=date(2026, 1, 1),
    )

    bound = bind_retired_membership(
        base,
        source,
        installed_event_rows=1,
        corporate_action_evidence={
            "receipt_id": "fixture",
            "source_options_date": "2026-01-01",
            "split_rows": 1,
            "dividend_rows": 1,
            "retired_split_rows": 1,
            "retired_dividend_rows": 1,
        },
    )

    assert bound["universe_id"] != "sha256:" + "0" * 64
    assert bound["source"]["retired_membership"]["earnings"]["row_count"] == 1
