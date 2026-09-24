from datetime import date

import duckdb
import pytest

from frontend_data.payloads import build_symbol_detail


def sparse_symbol_connection():
    conn = duckdb.connect()
    conn.execute('''CREATE TABLE earnings_events (
        ticker VARCHAR, earnings_dt DATE, timing VARCHAR, fiscal_year INTEGER,
        fiscal_q VARCHAR, eps_actual DOUBLE, eps_estimate DOUBLE,
        revenue_actual DOUBLE, revenue_estimate DOUBLE, source VARCHAR)''')
    conn.execute('''INSERT INTO earnings_events VALUES
        ('HUBG', '2026-02-05', 'after_market_close', 2025, 'Q4', .4, .3, 1000, 900, 'test'),
        ('HUBG', '2026-10-28', 'unknown', 2026, 'Q3', NULL, .48, NULL, 1100, 'test')''')
    conn.execute('''CREATE TABLE v_eligible_straddles (
        ticker VARCHAR, as_of_date DATE, expiry_date DATE, dte INTEGER,
        atm_strike DOUBLE, atm_iv DOUBLE, call_iv DOUBLE, put_iv DOUBLE,
        straddle_mid DOUBLE, straddle_pct DOUBLE, call_delta DOUBLE,
        call_gamma DOUBLE, call_vega DOUBLE, call_theta DOUBLE, quote_quality_status VARCHAR)''')
    conn.execute('CREATE TABLE v_ohlcv (act_symbol VARCHAR, date DATE, close DOUBLE)')
    conn.execute('''INSERT INTO v_ohlcv VALUES
        ('HUBG', '2026-02-05', 40), ('HUBG', '2026-02-06', 36),
        ('HUBG', '2026-09-21', 31.68)''')
    return conn


def test_missing_options_does_not_erase_independent_history_and_fundamentals():
    with sparse_symbol_connection() as conn:
        detail = build_symbol_detail(conn, 'HUBG', date(2026, 9, 21), None)
    assert detail is not None
    assert detail['spot_price'] == 31.68
    assert detail['straddle_features'] == []
    assert detail['expected_move'] is None
    past = next(row for row in detail['earnings_history'] if row['date'] == '2026-02-05')
    assert past['actual'] == pytest.approx(-.1)
    assert past['eps_actual'] == .4
    assert past['eps_surprise_pct'] == pytest.approx(1 / 3, abs=1e-6)
    assert past['implied'] is None


def test_valid_event_forecast_survives_missing_current_options():
    with sparse_symbol_connection() as conn:
        detail = build_symbol_detail(conn, 'HUBG', date(2026, 9, 21), date(2026, 10, 28), {
            ('HUBG', '2026-10-28'): {'em_ml_pct': .07, 'horizon_days': 21, 'as_of': '2026-09-21'},
        })
    assert detail is not None
    assert detail['expected_move']['earnings_date'] == '2026-10-28'
    assert detail['expected_move']['em_ml_pct'] == .07
    assert detail['expected_move'].get('atm_iv') is None
    assert detail['straddle_features'] == []
