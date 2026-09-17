from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb
import pytest

from frontend_data.display_forecast import DisplayPolicy, resolve_display_forecast


POLICY = DisplayPolicy(
    max_leg_relative_spread=1.0,
    max_straddle_relative_spread=0.75,
    max_atm_delta_distance=0.60,
    max_post_event_expiry_days=30,
    min_ticker_history_events=2,
    ticker_history_window_events=4,
    universe_prior_window_days=730,
)
AS_OF = date(2026, 9, 14)
EVENT = date(2026, 9, 23)
EXPIRY = date(2026, 10, 16)


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
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
            iv DOUBLE,
            delta DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE earnings_events (
            ticker VARCHAR,
            earnings_dt DATE,
            timing VARCHAR
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
    conn.execute("INSERT INTO v_ohlcv VALUES (?, 'PAYX', 122.0)", [AS_OF])
    return conn


def _option(
    conn: duckdb.DuckDBPyConnection,
    *,
    strike: float,
    side: str,
    iv: float | None,
    delta: float | None,
    bid: float,
    ask: float,
    expiry: date = EXPIRY,
) -> None:
    conn.execute(
        "INSERT INTO v_options VALUES (?, 'PAYX', ?, ?, ?, ?, ?, ?, ?)",
        [AS_OF, expiry, strike, side, bid, ask, iv, delta],
    )


def _history(conn: duckdb.DuckDBPyConnection, moves: list[float]) -> None:
    for idx, move in enumerate(moves, start=1):
        event = EVENT - timedelta(days=90 * idx)
        conn.execute(
            "INSERT INTO earnings_events VALUES ('PAYX', ?, 'unknown')",
            [event],
        )
        conn.executemany(
            "INSERT INTO v_ohlcv VALUES (?, 'PAYX', ?)",
            [
                (event - timedelta(days=1), 100.0),
                (event + timedelta(days=1), 100.0 * (1.0 + move)),
            ],
        )


def _resolve(conn: duckdb.DuckDBPyConnection):
    return resolve_display_forecast(
        conn,
        ticker="PAYX",
        earnings_date=EVENT,
        timing="bmo",
        as_of_date=AS_OF,
        ml_forecast=None,
        strict_options=None,
        universe_prior={
            "as_of_date": AS_OF.isoformat(),
            "median_abs_move": 0.055,
            "event_count": 100,
            "symbol_count": 50,
        },
        policy=POLICY,
    )


def test_mismatched_strikes_use_independent_iv_before_history():
    conn = _conn()
    _option(
        conn,
        strike=120.0,
        side="Call",
        iv=0.40,
        delta=0.50,
        bid=2.0,
        ask=3.0,
    )
    _option(
        conn,
        strike=125.0,
        side="Put",
        iv=0.44,
        delta=-0.50,
        bid=5.0,
        ask=6.0,
    )
    _history(conn, [0.02, 0.04, 0.06, 0.08])

    result = _resolve(conn)

    assert result.method == "options_indicative"
    assert result.fallback_reason == "no_same_strike_pair"
    assert result.selected_options_details is not None
    assert result.selected_options_details["estimator"] == "atm_iv"
    assert result.selected_options_details["sides_used"] == ["C", "P"]
    expected = 0.42 * math.sqrt((EXPIRY - AS_OF).days / 365.0)
    assert result.pct == pytest.approx(expected)


def test_one_clean_side_can_supply_indicative_iv():
    conn = _conn()
    _option(
        conn,
        strike=120.0,
        side="Call",
        iv=0.40,
        delta=0.50,
        bid=2.0,
        ask=3.0,
    )
    _option(
        conn,
        strike=120.0,
        side="Put",
        iv=0.44,
        delta=-0.50,
        bid=0.01,
        ask=5.01,
    )

    result = _resolve(conn)

    assert result.method == "options_indicative"
    assert result.fallback_reason == "quote_quality"
    assert result.selected_options_details is not None
    assert result.selected_options_details["estimator"] == "atm_iv"
    assert result.selected_options_details["sides_used"] == ["C"]
    expected = 0.40 * math.sqrt((EXPIRY - AS_OF).days / 365.0)
    assert result.pct == pytest.approx(expected)


def test_invalid_iv_still_falls_back_to_history():
    conn = _conn()
    _option(
        conn,
        strike=120.0,
        side="Call",
        iv=None,
        delta=0.50,
        bid=2.0,
        ask=3.0,
    )
    _option(
        conn,
        strike=125.0,
        side="Put",
        iv=6.0,
        delta=-0.50,
        bid=5.0,
        ask=6.0,
    )
    _history(conn, [0.02, 0.04, 0.06, 0.08])

    result = _resolve(conn)

    assert result.method == "historical"
    assert result.pct == pytest.approx(0.05)
