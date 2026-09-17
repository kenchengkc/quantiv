from __future__ import annotations

from datetime import date

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


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE v_options (
            date DATE, act_symbol VARCHAR, expiration DATE, strike DOUBLE,
            call_put VARCHAR, bid DOUBLE, ask DOUBLE, delta DOUBLE
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


@pytest.mark.parametrize(
    "status",
    ["unavailable_inputs", "unavailable_model", "unavailable_event"],
)
def test_missing_ml_preserves_explicit_unavailability_reason(status: str) -> None:
    result = resolve_display_forecast(
        _conn(),
        ticker="PAYX",
        earnings_date=date(2026, 9, 23),
        timing="bmo",
        as_of_date=date(2026, 9, 14),
        ml_forecast={"em_ml_pct": None, "ml_status": status},
        strict_options={"em_baseline_straddle": 0.08},
        universe_prior={
            "as_of_date": "2026-09-14",
            "median_abs_move": 0.055,
            "event_count": 100,
            "symbol_count": 50,
        },
        policy=POLICY,
    )

    assert result.method == "options_math"
    assert result.ml_status == status
