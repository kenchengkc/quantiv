from __future__ import annotations

from datetime import date

import duckdb

from frontend_data.research_cutoff import restrict_ohlcv_to_as_of
from frontend_data.research_history import build_historical_event_universe


def _base_research_tables(conn: duckdb.DuckDBPyConnection) -> None:
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
    conn.execute(
        """
        CREATE TABLE v_eligible_straddles (
            ticker VARCHAR,
            as_of_date DATE,
            expiry_date DATE,
            dte INTEGER,
            atm_strike DOUBLE,
            straddle_mid DOUBLE,
            straddle_pct DOUBLE,
            atm_iv DOUBLE,
            quote_quality_status VARCHAR,
            call_quote_timestamp TIMESTAMP,
            put_quote_timestamp TIMESTAMP,
            straddle_relative_spread DOUBLE,
            atm_delta_distance DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE v_splits (
            act_symbol VARCHAR, ex_date DATE, to_factor DOUBLE, for_factor DOUBLE
        )
        """
    )
    conn.execute(
        "CREATE TABLE v_dividends (act_symbol VARCHAR, ex_date DATE, amount DOUBLE)"
    )


def test_research_cutoff_physically_hides_future_prices() -> None:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE _raw_ohlcv AS
        SELECT DATE '2026-01-09' AS date, 'AAPL'::VARCHAR AS act_symbol, 100.0::DOUBLE AS close
        UNION ALL
        SELECT DATE '2026-01-10', 'AAPL', 101.0
        UNION ALL
        SELECT DATE '2026-01-11', 'AAPL', 120.0
        """
    )
    conn.execute("CREATE VIEW v_ohlcv AS SELECT * FROM _raw_ohlcv")

    evidence = restrict_ohlcv_to_as_of(conn, date(2026, 1, 10))

    assert evidence["source_rows"] == 3
    assert evidence["retained_rows"] == 2
    assert evidence["dropped_post_cutoff_rows"] == 1
    assert conn.execute("SELECT MAX(date) FROM v_ohlcv").fetchone()[0] == date(2026, 1, 10)


def test_near_cutoff_event_cannot_use_future_realized_close() -> None:
    conn = duckdb.connect()
    _base_research_tables(conn)
    conn.execute(
        """
        INSERT INTO earnings_events VALUES (
            'AAPL', DATE '2026-01-09', 'after_market_close', 'fixture', TRUE,
            2026, 'Q1', NULL, NULL, NULL, NULL, 'reported'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO v_eligible_straddles VALUES (
            'AAPL', DATE '2026-01-09', DATE '2026-01-16', 7,
            100, 8, 0.08, 0.35, 'decision_eligible_eod',
            NULL, NULL, 0.05, 0.02
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE _raw_ohlcv AS
        SELECT DATE '2026-01-09' AS date, 'AAPL'::VARCHAR AS act_symbol, 100.0::DOUBLE AS close
        UNION ALL
        SELECT DATE '2026-01-11', 'AAPL', 120.0
        """
    )
    conn.execute("CREATE VIEW v_ohlcv AS SELECT * FROM _raw_ohlcv")

    restrict_ohlcv_to_as_of(conn, date(2026, 1, 10))
    payload = build_historical_event_universe(
        conn,
        date(2026, 1, 10),
        source_revision="fixture",
    )

    assert payload["event_count"] == 0
    assert payload["audit"]["candidate_event_count"] == 1
    assert payload["audit"]["excluded_event_count"] == 1
    assert payload["audit"]["exclusions"][0]["reasons"] == [
        "missing_realized_price_window"
    ]
