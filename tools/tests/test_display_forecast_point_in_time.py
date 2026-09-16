from __future__ import annotations

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
TARGET = date(2026, 9, 23)


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
            delta DOUBLE
        )
        """
    )
    conn.execute(
        "CREATE TABLE earnings_events (ticker VARCHAR, earnings_dt DATE, timing VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE v_ohlcv (date DATE, act_symbol VARCHAR, close DOUBLE)"
    )
    return conn


def _resolve(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str = "PAYX",
    universe_prior: dict | None = None,
):
    return resolve_display_forecast(
        conn,
        ticker=ticker,
        earnings_date=TARGET,
        timing="bmo",
        as_of_date=AS_OF,
        ml_forecast=None,
        strict_options=None,
        universe_prior=universe_prior,
        policy=POLICY,
    )


def test_ticker_history_ignores_realization_not_observable_by_cutoff():
    conn = _conn()
    known_event = date(2026, 9, 10)
    unresolved_event = date(2026, 9, 13)
    conn.executemany(
        "INSERT INTO earnings_events VALUES ('PAYX', ?, 'unknown')",
        [(known_event,), (unresolved_event,)],
    )
    conn.executemany(
        "INSERT INTO v_ohlcv VALUES (?, 'PAYX', ?)",
        [
            (known_event - timedelta(days=1), 100.0),
            (known_event + timedelta(days=1), 104.0),
            (unresolved_event - timedelta(days=1), 100.0),
            # This realization is after the forecast cutoff and must not count.
            (AS_OF + timedelta(days=1), 120.0),
        ],
    )

    result = _resolve(
        conn,
        universe_prior={
            "as_of_date": AS_OF.isoformat(),
            "median_abs_move": 0.055,
            "event_count": 100,
            "symbol_count": 50,
        },
    )

    assert result.method == "historical_prior"
    assert result.pct == pytest.approx(0.055)
    assert result.historical_event_count == 1


def test_universe_prior_ignores_realization_not_observable_by_cutoff():
    conn = _conn()
    known_event = date(2026, 9, 10)
    unresolved_event = date(2026, 9, 13)
    conn.executemany(
        "INSERT INTO earnings_events VALUES (?, ?, 'unknown')",
        [("KNOWN", known_event), ("UNRESOLVED", unresolved_event)],
    )
    conn.executemany(
        "INSERT INTO v_ohlcv VALUES (?, ?, ?)",
        [
            (known_event - timedelta(days=1), "KNOWN", 100.0),
            (known_event + timedelta(days=1), "KNOWN", 104.0),
            (unresolved_event - timedelta(days=1), "UNRESOLVED", 100.0),
            # This realization is after the forecast cutoff and must not count.
            (AS_OF + timedelta(days=1), "UNRESOLVED", 120.0),
        ],
    )

    result = _resolve(conn, ticker="NEW", universe_prior=None)

    assert result.method == "historical_prior"
    assert result.pct == pytest.approx(0.04)
    assert result.as_of == AS_OF.isoformat()
