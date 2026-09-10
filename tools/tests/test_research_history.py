from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from frontend_data.research_history import build_historical_event_universe


def _tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE earnings_events (
            ticker VARCHAR,
            earnings_dt DATE,
            timing VARCHAR,
            timing_source VARCHAR,
            source VARCHAR,
            fiscal_year BIGINT,
            fiscal_q VARCHAR,
            eps_actual DOUBLE,
            eps_estimate DOUBLE,
            revenue_actual DOUBLE,
            revenue_estimate DOUBLE
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
        CREATE TABLE v_ohlcv (
            date DATE,
            act_symbol VARCHAR,
            close DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE v_splits (
            act_symbol VARCHAR,
            ex_date DATE,
            to_factor DOUBLE,
            for_factor DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE v_dividends (
            act_symbol VARCHAR,
            ex_date DATE,
            amount DOUBLE
        )
        """
    )


def _event(
    conn: duckdb.DuckDBPyConnection,
    ticker: str,
    event_date: date,
    *,
    timing: str = "before_market_open",
    timing_source: str = "reported",
    option_date: date | None = None,
    pre_date: date | None = None,
    post_date: date | None = None,
    pre_price: float = 100.0,
    post_price: float = 110.0,
) -> None:
    conn.execute(
        "INSERT INTO earnings_events VALUES (?, ?, ?, ?, 'fixture', 2025, 'Q1', 1.1, 1.0, 110, 100)",
        [ticker, event_date, timing, timing_source],
    )
    option_date = option_date or event_date - timedelta(days=1)
    conn.execute(
        """
        INSERT INTO v_eligible_straddles VALUES (
            ?, ?, ?, 2, 100, 10, 0.10, 0.40, 'decision_eligible_eod',
            NULL, NULL, 0.05, 0.02
        )
        """,
        [ticker, option_date, event_date + timedelta(days=1)],
    )
    if pre_date is None:
        pre_date = event_date - timedelta(days=1)
    if post_date is None:
        post_date = event_date if timing == "before_market_open" else event_date + timedelta(days=1)
    conn.execute("INSERT INTO v_ohlcv VALUES (?, ?, ?)", [pre_date, ticker, pre_price])
    conn.execute("INSERT INTO v_ohlcv VALUES (?, ?, ?)", [post_date, ticker, post_price])


def test_source_universe_keeps_thirteenth_event_independent_of_ticker_display_limit() -> None:
    conn = duckdb.connect()
    _tables(conn)
    start = date(2024, 1, 10)
    for index in range(13):
        _event(conn, "LONG", start + timedelta(days=index * 28))

    payload = build_historical_event_universe(
        conn,
        date(2026, 1, 1),
        source_revision="fixture-sha",
    )

    assert payload["source"]["kind"] == "analytical_duckdb"
    assert payload["source"]["completeness"] == "source_level"
    assert payload["event_count"] == 13
    assert payload["audit"]["candidate_event_count"] == 13
    assert payload["audit"]["excluded_event_count"] == 0
    assert len([event for event in payload["events"] if event["ticker"] == "LONG"]) == 13
    assert payload["universe_id"].startswith("sha256:")


def test_session_boundaries_match_bmo_amc_and_unknown_rules() -> None:
    conn = duckdb.connect()
    _tables(conn)
    bmo = date(2025, 4, 10)
    amc = date(2025, 5, 10)
    unknown = date(2025, 6, 10)

    _event(conn, "BMO", bmo, timing="before_market_open", pre_date=bmo - timedelta(days=1), post_date=bmo)
    _event(
        conn,
        "AMC",
        amc,
        timing="after_market_close",
        option_date=amc,
        pre_date=amc,
        post_date=amc + timedelta(days=1),
    )
    _event(conn, "UNK", unknown, timing="unknown", pre_date=unknown - timedelta(days=1), post_date=unknown + timedelta(days=1))

    payload = build_historical_event_universe(conn, date(2026, 1, 1))
    by_ticker = {row["ticker"]: row for row in payload["events"]}

    assert by_ticker["BMO"]["realized_window"]["pre_date"] == "2025-04-09"
    assert by_ticker["BMO"]["realized_window"]["post_date"] == "2025-04-10"
    assert by_ticker["AMC"]["implied_as_of"] == "2025-05-10"
    assert by_ticker["AMC"]["realized_window"]["pre_date"] == "2025-05-10"
    assert by_ticker["AMC"]["realized_window"]["post_date"] == "2025-05-11"
    assert by_ticker["UNK"]["realized_window"]["pre_date"] == "2025-06-09"
    assert by_ticker["UNK"]["realized_window"]["post_date"] == "2025-06-11"


def test_inferred_timing_does_not_use_same_day_option_as_historical_amc_evidence() -> None:
    conn = duckdb.connect()
    _tables(conn)
    event_date = date(2025, 7, 10)
    _event(
        conn,
        "INF",
        event_date,
        timing="after_market_close",
        timing_source="inferred_majority_3",
        option_date=event_date,
        pre_date=event_date - timedelta(days=1),
        post_date=event_date + timedelta(days=1),
    )

    payload = build_historical_event_universe(conn, date(2026, 1, 1))

    assert payload["event_count"] == 0
    assert payload["audit"]["excluded_event_count"] == 1
    exclusion = payload["audit"]["exclusions"][0]
    assert exclusion["ticker"] == "INF"
    assert exclusion["timing"] == "unknown"
    assert exclusion["timing_source"] == "inferred_majority_3"
    assert "missing_eligible_pre_event_straddle" in exclusion["reasons"]


def test_realized_move_normalizes_split_between_price_endpoints() -> None:
    conn = duckdb.connect()
    _tables(conn)
    event_date = date(2025, 8, 10)
    _event(
        conn,
        "SPLT",
        event_date,
        timing="unknown",
        pre_date=event_date - timedelta(days=1),
        post_date=event_date + timedelta(days=1),
        pre_price=100.0,
        post_price=50.0,
    )
    conn.execute(
        "INSERT INTO v_splits VALUES ('SPLT', ?, 2.0, 1.0)",
        [event_date + timedelta(days=1)],
    )

    payload = build_historical_event_universe(conn, date(2026, 1, 1))
    event = payload["events"][0]

    assert event["actual"] == pytest.approx(0.0)
    assert event["realized_window"]["post_price_raw"] == 50.0
    assert event["realized_window"]["post_price_adjusted"] == 100.0
    assert event["realized_window"]["split_actions"] == 1


def test_source_duplicate_identity_is_collapsed_and_audited() -> None:
    conn = duckdb.connect()
    _tables(conn)
    event_date = date(2025, 9, 10)
    _event(conn, "DUP", event_date)
    conn.execute(
        """
        INSERT INTO earnings_events VALUES (
            'DUP', ?, 'after_market_close', 'inferred_majority_3',
            'older-revision', 2025, 'Q1', NULL, NULL, NULL, NULL
        )
        """,
        [event_date],
    )

    payload = build_historical_event_universe(conn, date(2026, 1, 1))

    assert payload["event_count"] == 1
    assert payload["events"][0]["timing"] == "before_market_open"
    assert payload["events"][0]["event_provenance"]["source_row_count"] == 2
    assert payload["audit"]["source_duplicate_rows_collapsed"] == 1
