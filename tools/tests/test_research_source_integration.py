from __future__ import annotations

from datetime import date

import duckdb

from frontend_data.research_history import build_historical_event_universe
from frontend_data.research_sources import install_retired_earnings


def test_retired_company_can_become_eligible_research_event() -> None:
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
        "CREATE TABLE v_ohlcv (date DATE, act_symbol VARCHAR, close DOUBLE)"
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

    added = install_retired_earnings(
        conn,
        [
            {
                "ticker": "OLD",
                "date": "2025-04-10",
                "timing": "before_market_open",
            }
        ],
    )
    assert added == 1

    conn.execute(
        """
        INSERT INTO v_eligible_straddles VALUES (
            'OLD', DATE '2025-04-09', DATE '2025-04-11', 2,
            100, 8, 0.08, 0.35, 'decision_eligible_eod',
            NULL, NULL, 0.05, 0.02
        )
        """
    )
    conn.execute(
        "INSERT INTO v_ohlcv VALUES (DATE '2025-04-09', 'OLD', 100)"
    )
    conn.execute(
        "INSERT INTO v_ohlcv VALUES (DATE '2025-04-10', 'OLD', 109)"
    )

    payload = build_historical_event_universe(
        conn,
        date(2026, 1, 1),
        source_revision="fixture",
    )

    assert payload["event_count"] == 1
    event = payload["events"][0]
    assert event["ticker"] == "OLD"
    assert event["event_provenance"]["source"] == "dolthub_retired_membership"
    assert event["actual"] == 0.09
    assert payload["audit"]["excluded_event_count"] == 0
