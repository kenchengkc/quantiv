from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from frontend_data.display_forecast import (
    DisplayPolicy,
    resolve_display_forecast,
)


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
    return conn


def _pair(
    conn: duckdb.DuckDBPyConnection,
    *,
    strike: float = 120,
    call_bid: float = 2.65,
    call_ask: float = 4.50,
    call_delta: float | None = 0.4504,
    put_bid: float = 5.10,
    put_ask: float = 6.80,
    put_delta: float | None = -0.5398,
    expiry: date = EXPIRY,
) -> None:
    conn.executemany(
        "INSERT INTO v_options VALUES (?, 'PAYX', ?, ?, ?, ?, ?, ?)",
        [
            (AS_OF, expiry, strike, "Call", call_bid, call_ask, call_delta),
            (AS_OF, expiry, strike, "Put", put_bid, put_ask, put_delta),
        ],
    )


def _history(
    conn: duckdb.DuckDBPyConnection,
    ticker: str,
    moves: list[float],
    *,
    before: date = EVENT,
) -> None:
    for idx, move in enumerate(moves, start=1):
        event = before - timedelta(days=90 * idx)
        pre = event - timedelta(days=1)
        post = event + timedelta(days=1)
        conn.execute(
            "INSERT INTO earnings_events VALUES (?, ?, 'unknown')",
            [ticker, event],
        )
        conn.executemany(
            "INSERT INTO v_ohlcv VALUES (?, ?, ?)",
            [
                (pre, ticker, 100.0),
                (post, ticker, 100.0 * (1.0 + move)),
            ],
        )


def _resolve(conn: duckdb.DuckDBPyConnection, **kwargs):
    return resolve_display_forecast(
        conn,
        ticker=kwargs.pop("ticker", "PAYX"),
        earnings_date=kwargs.pop("earnings_date", EVENT),
        timing=kwargs.pop("timing", "bmo"),
        as_of_date=kwargs.pop("as_of_date", AS_OF),
        ml_forecast=kwargs.pop("ml_forecast", None),
        strict_options=kwargs.pop("strict_options", None),
        universe_prior=kwargs.pop(
            "universe_prior",
            {
                "as_of_date": EVENT.isoformat(),
                "median_abs_move": 0.055,
                "event_count": 100,
                "symbol_count": 50,
            },
        ),
        policy=POLICY,
        **kwargs,
    )


def test_ml_wins_over_all_fallbacks():
    conn = _conn()
    _pair(conn)
    result = _resolve(
        conn,
        ml_forecast={"em_ml_pct": 0.041},
        strict_options={"em_baseline_straddle": 0.081},
    )
    assert result.method == "ml"
    assert result.pct == 0.041
    assert result.ml_status == "available"
    assert result.options_status == "decision_eligible"


def test_ml_display_as_of_uses_model_snapshot_date():
    conn = _conn()
    result = _resolve(
        conn,
        ml_forecast={
            "em_ml_pct": 0.041,
            "ml_snapshot_date": "2026-09-09",
        },
    )
    assert result.method == "ml"
    assert result.as_of == "2026-09-09"


def test_strict_options_used_when_ml_missing():
    conn = _conn()
    result = _resolve(
        conn,
        strict_options={
            "em_baseline_straddle": 0.081,
            "expiry_date": "2026-10-16",
            "atm_strike": 120.0,
            "straddle_price": 9.72,
        },
    )
    assert result.method == "options_math"
    assert result.pct == 0.081
    assert result.options_status == "decision_eligible"
    assert result.ml_status == "unavailable_inputs"


def test_payx_like_pair_becomes_indicative():
    conn = _conn()
    _pair(conn)
    result = _resolve(conn)
    assert result.method == "options_indicative"
    assert result.options_status == "indicative"
    assert result.fallback_reason == "quote_quality"
    assert result.selected_options_details is not None
    assert result.selected_options_details["call_relative_spread"] > 0.50
    assert result.selected_options_details["call_relative_spread"] < 1.0
    assert result.pct > 0


def test_indicative_expected_move_uses_eod_spot_not_atm_strike():
    conn = _conn()
    conn.execute(
        "INSERT INTO v_ohlcv VALUES (?, 'PAYX', 100.0)",
        [AS_OF],
    )
    _pair(
        conn,
        strike=120.0,
        call_bid=2.0,
        call_ask=3.0,
        call_delta=0.5,
        put_bid=7.0,
        put_ask=8.0,
        put_delta=-0.5,
    )

    result = _resolve(conn)

    assert result.method == "options_indicative"
    assert result.pct == pytest.approx(0.10)
    assert result.selected_options_details is not None
    assert result.selected_options_details["estimated_spot"] == pytest.approx(100.0)


def test_indicative_selection_advances_to_next_spanning_expiry():
    conn = _conn()
    first_expiry = date(2026, 9, 25)
    second_expiry = date(2026, 10, 2)
    # Nearest expiry is structurally valid but too wide for the display policy.
    _pair(
        conn,
        expiry=first_expiry,
        call_bid=0.05,
        call_ask=2.05,
        put_bid=0.05,
        put_ask=2.05,
    )
    # The next spanning expiry is usable and should be selected instead of
    # abandoning options and falling through to historical context.
    _pair(
        conn,
        expiry=second_expiry,
        call_bid=2.0,
        call_ask=3.0,
        put_bid=2.0,
        put_ask=3.0,
        call_delta=0.5,
        put_delta=-0.5,
    )

    result = _resolve(conn)

    assert result.method == "options_indicative"
    assert result.selected_options_details is not None
    assert result.selected_options_details["expiry_date"] == second_expiry.isoformat()


def test_ful_like_pair_rejects_190pct_leg_and_uses_history():
    conn = _conn()
    # Put midpoint 1.05, spread 2.0 => ~190.5% relative spread.
    _pair(
        conn,
        call_bid=2.65,
        call_ask=4.50,
        put_bid=0.05,
        put_ask=2.05,
    )
    _history(conn, "PAYX", [0.03, 0.05, 0.07, 0.09])
    result = _resolve(conn)
    assert result.method == "historical"
    assert result.pct == pytest.approx(0.06)
    assert result.options_status == "unavailable"
    assert result.fallback_reason == "quote_quality"
    assert result.historical_event_count == 4


def test_no_same_strike_pair_uses_history():
    conn = _conn()
    conn.executemany(
        "INSERT INTO v_options VALUES (?, 'PAYX', ?, ?, ?, ?, ?, ?)",
        [
            (AS_OF, EXPIRY, 120.0, "Call", 2.0, 3.0, 0.5),
            (AS_OF, EXPIRY, 125.0, "Put", 5.0, 6.0, -0.5),
        ],
    )
    _history(conn, "PAYX", [0.02, 0.04])
    result = _resolve(conn)
    assert result.method == "historical"
    assert result.fallback_reason == "no_same_strike_pair"


def test_one_prior_event_uses_universe_prior():
    conn = _conn()
    _history(conn, "PAYX", [0.02])
    result = _resolve(conn)
    assert result.method == "historical_prior"
    assert result.pct == 0.055
    assert result.fallback_reason == "insufficient_ticker_history"
    assert result.historical_event_count == 1


def test_ticker_history_does_not_use_events_after_forecast_cutoff():
    conn = _conn()
    known_event = date(2026, 9, 10)
    future_event = date(2026, 9, 18)
    conn.executemany(
        "INSERT INTO earnings_events VALUES ('PAYX', ?, 'unknown')",
        [(known_event,), (future_event,)],
    )
    conn.executemany(
        "INSERT INTO v_ohlcv VALUES (?, 'PAYX', ?)",
        [
            (known_event - timedelta(days=1), 100.0),
            (known_event + timedelta(days=1), 104.0),
            (future_event - timedelta(days=1), 100.0),
            (future_event + timedelta(days=1), 120.0),
        ],
    )

    result = _resolve(conn)

    assert result.method == "historical_prior"
    assert result.pct == pytest.approx(0.055)
    assert result.historical_event_count == 1


def test_generated_universe_prior_uses_forecast_cutoff_not_target_date():
    conn = _conn()
    known_event = date(2026, 9, 10)
    future_event = date(2026, 9, 18)
    conn.executemany(
        "INSERT INTO earnings_events VALUES (?, ?, 'unknown')",
        [
            ("KNOWN", known_event),
            ("FUTURE", future_event),
        ],
    )
    conn.executemany(
        "INSERT INTO v_ohlcv VALUES (?, ?, ?)",
        [
            (known_event - timedelta(days=1), "KNOWN", 100.0),
            (known_event + timedelta(days=1), "KNOWN", 104.0),
            # These rows deliberately simulate information that exists before
            # the target earnings event but only after the forecast cutoff.
            (future_event - timedelta(days=1), "FUTURE", 100.0),
            (future_event + timedelta(days=1), "FUTURE", 120.0),
        ],
    )

    result = _resolve(conn, universe_prior=None)

    assert result.method == "historical_prior"
    assert result.pct == pytest.approx(0.04)
    assert result.as_of == AS_OF.isoformat()


def test_crossed_quote_is_never_indicative():
    conn = _conn()
    _pair(conn, call_bid=4.50, call_ask=2.65)
    _history(conn, "PAYX", [0.02, 0.04])
    result = _resolve(conn)
    assert result.method == "historical"
    assert result.fallback_reason in {"no_same_strike_pair", "quote_quality"}


def test_amc_same_day_expiry_rejected_but_bmo_same_day_expiry_allowed():
    conn = _conn()
    _pair(conn, expiry=EVENT)
    _history(conn, "PAYX", [0.02, 0.04])

    bmo = _resolve(conn, timing="bmo")
    amc = _resolve(conn, timing="amc")

    assert bmo.method == "options_indicative"
    assert amc.method == "historical"
    assert amc.fallback_reason == "no_event_expiry"


def test_missing_deltas_fall_back_to_strike_proximity():
    conn = _conn()
    _pair(conn, call_delta=None, put_delta=None)
    result = _resolve(conn)
    assert result.method == "options_indicative"
    assert result.selected_options_details is not None
    assert result.selected_options_details["atm_delta_distance"] is None
