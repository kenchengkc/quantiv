from __future__ import annotations

from datetime import date

import duckdb

from frontend_data.research_history import build_historical_event_universe


def test_source_snapshot_excludes_rows_after_research_cutoff() -> None:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE earnings_events (
            ticker VARCHAR, earnings_dt DATE, timing VARCHAR, timing_source VARCHAR,
            source VARCHAR, fiscal_year BIGINT, fiscal_q VARCHAR,
            eps_actual DOUBLE, eps_estimate DOUBLE,
            revenue_actual DOUBLE, revenue_estimate DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE v_eligible_straddles (
            ticker VARCHAR, as_of_date DATE, expiry_date DATE, dte INTEGER,
            atm_strike DOUBLE, straddle_mid DOUBLE, straddle_pct DOUBLE, atm_iv DOUBLE,
            quote_quality_status VARCHAR, call_quote_timestamp TIMESTAMP,
            put_quote_timestamp TIMESTAMP, straddle_relative_spread DOUBLE,
            atm_delta_distance DOUBLE
        )
        """
    )
    conn.execute("CREATE TABLE v_ohlcv (date DATE, act_symbol VARCHAR, close DOUBLE)")
    conn.execute(
        "CREATE TABLE v_splits (act_symbol VARCHAR, ex_date DATE, to_factor DOUBLE, for_factor DOUBLE)"
    )
    conn.execute(
        "CREATE TABLE v_dividends (act_symbol VARCHAR, ex_date DATE, amount DOUBLE)"
    )

    conn.execute(
        """
        INSERT INTO earnings_events VALUES (
            'OLD', DATE '2025-04-10', 'before_market_open', 'reported',
            'fixture', 2025, 'Q2', 1.1, 1.0, 110, 100
        )
        """
    )
    conn.execute(
        """
        INSERT INTO v_eligible_straddles VALUES
          ('OLD', DATE '2025-04-09', DATE '2025-04-11', 2, 100, 8, 0.08, 0.35,
           'decision_eligible_eod', NULL, NULL, 0.05, 0.02),
          ('FUT', DATE '2026-02-01', DATE '2026-02-06', 5, 100, 9, 0.09, 0.36,
           'decision_eligible_eod', NULL, NULL, 0.05, 0.02)
        """
    )
    conn.execute(
        """
        INSERT INTO v_ohlcv VALUES
          (DATE '2025-04-09', 'OLD', 100),
          (DATE '2025-04-10', 'OLD', 109),
          (DATE '2026-02-01', 'FUT', 200)
        """
    )

    payload = build_historical_event_universe(conn, date(2026, 1, 1))
    source = payload["source"]

    assert payload["event_count"] == 1
    assert source["eligible_straddle_rows"] == 1
    assert source["eligible_straddle_as_of_max"] == "2025-04-09"
    assert source["ohlcv_rows"] == 2
    assert source["ohlcv_date_max"] == "2025-04-10"
