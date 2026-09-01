from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys

import duckdb
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_earnings_events import create_duckdb_views  # noqa: E402
from frontend_data.payloads import (  # noqa: E402
    _historical_option_evidence,
    build_symbol_detail,
)
from math_baseline import _is_pre_event_observation  # noqa: E402


def test_eod_observation_must_precede_the_event_session() -> None:
    event = date(2026, 2, 10)

    assert _is_pre_event_observation(date(2026, 2, 9), event, "before_market_open")
    assert not _is_pre_event_observation(event, event, "before_market_open")
    assert _is_pre_event_observation(event, event, "after_market_close")
    assert not _is_pre_event_observation(date(2026, 2, 11), event, "after_market_close")
    assert not _is_pre_event_observation(event, event, "unknown")


def test_historical_evidence_respects_bmo_and_amc_observation_cutoffs() -> None:
    conn = duckdb.connect()
    try:
        conn.execute(
            """
            CREATE TABLE earnings_events (
                ticker VARCHAR, earnings_dt DATE, timing VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO earnings_events VALUES
                ('ACME', '2026-02-10', 'before_market_open'),
                ('ACME', '2026-05-10', 'after_market_close')
            """
        )
        conn.execute(
            """
            CREATE TABLE v_eligible_straddles (
                ticker VARCHAR, as_of_date DATE, expiry_date DATE, dte INTEGER,
                atm_strike DOUBLE, straddle_mid DOUBLE, straddle_pct DOUBLE,
                atm_iv DOUBLE, quote_quality_status VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO v_eligible_straddles VALUES
                ('ACME', '2026-02-09', '2026-02-13', 4, 100, 6, .06, .42,
                 'decision_eligible_eod'),
                ('ACME', '2026-02-10', '2026-02-13', 3, 101, 7, .07, .44,
                 'decision_eligible_eod'),
                ('ACME', '2026-05-09', '2026-05-15', 6, 110, 8, .072, .46,
                 'decision_eligible_eod'),
                ('ACME', '2026-05-10', '2026-05-15', 5, 111, 9, .081, .48,
                 'decision_eligible_eod'),
                ('ACME', '2026-05-10', '2026-05-10', 0, 111, 5, .045, .40,
                 'decision_eligible_eod')
            """
        )

        evidence = _historical_option_evidence(conn, "ACME", date(2026, 6, 1))
    finally:
        conn.close()

    assert evidence[date(2026, 2, 10)]["implied_as_of"] == "2026-02-09"
    assert evidence[date(2026, 2, 10)]["implied_lead_days"] == 1
    assert evidence[date(2026, 5, 10)]["implied_as_of"] == "2026-05-10"
    assert evidence[date(2026, 5, 10)]["implied_expiration"] == "2026-05-15"


def _option(
    symbol: str,
    side: str,
    *,
    bid: float,
    ask: float,
    delta: float,
    expiration: date = date(2026, 2, 20),
    quote_timestamp: datetime = datetime(2026, 2, 10, 20, 0),
    option_volume: int = 100,
    open_interest: int = 500,
) -> dict[str, object]:
    return {
        "date": date(2026, 2, 10),
        "act_symbol": symbol,
        "expiration": expiration,
        "strike": 100.0,
        "call_put": side,
        "bid": bid,
        "ask": ask,
        "vol": 0.4,
        "delta": delta,
        "gamma": 0.1,
        "theta": -0.1,
        "vega": 0.1,
        "quote_timestamp": quote_timestamp,
        "option_volume": option_volume,
        "open_interest": open_interest,
    }


def test_frontend_straddles_use_the_same_leg_and_pair_quality_gates(
    tmp_path: Path,
) -> None:
    rows = [
        _option("GOOD", "Call", bid=4.0, ask=4.2, delta=0.5),
        _option("GOOD", "Put", bid=3.8, ask=4.0, delta=-0.5),
        _option("WIDE", "Call", bid=0.1, ask=1.0, delta=0.5),
        _option("WIDE", "Put", bid=0.1, ask=1.0, delta=-0.5),
        _option("FAR", "Call", bid=1.0, ask=1.1, delta=0.1),
        _option("FAR", "Put", bid=1.0, ask=1.1, delta=-0.1),
        _option(
            "LONG",
            "Call",
            bid=4.0,
            ask=4.2,
            delta=0.5,
            expiration=date(2026, 4, 30),
        ),
        _option(
            "STALE",
            "Call",
            bid=4.0,
            ask=4.2,
            delta=0.5,
            quote_timestamp=datetime(2026, 2, 9, 20, 0),
        ),
        _option(
            "STALE",
            "Put",
            bid=3.8,
            ask=4.0,
            delta=-0.5,
            quote_timestamp=datetime(2026, 2, 9, 20, 0),
        ),
        _option(
            "ILLIQ",
            "Call",
            bid=4.0,
            ask=4.2,
            delta=0.5,
            option_volume=0,
            open_interest=0,
        ),
        _option(
            "ILLIQ",
            "Put",
            bid=3.8,
            ask=4.0,
            delta=-0.5,
            option_volume=0,
            open_interest=0,
        ),
        _option(
            "SKEW",
            "Call",
            bid=4.0,
            ask=4.2,
            delta=0.5,
            quote_timestamp=datetime(2026, 2, 10, 20, 0),
        ),
        _option(
            "SKEW",
            "Put",
            bid=3.8,
            ask=4.0,
            delta=-0.5,
            quote_timestamp=datetime(2026, 2, 10, 20, 2),
        ),
        _option(
            "LONG",
            "Put",
            bid=3.8,
            ask=4.0,
            delta=-0.5,
            expiration=date(2026, 4, 30),
        ),
    ]
    partition = (
        tmp_path
        / "parquet"
        / "options_chain"
        / "year=2026"
        / "month=02"
        / "2026-02-10.parquet"
    )
    partition.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(partition, index=False)
    conn = duckdb.connect()
    try:
        create_duckdb_views(conn, tmp_path)
        eligible = conn.execute(
            "SELECT ticker, quote_quality_status FROM v_eligible_straddles"
        ).fetchall()
    finally:
        conn.close()

    assert eligible == [("GOOD", "decision_eligible_eod")]


def test_known_event_never_falls_back_to_an_expiry_before_the_event() -> None:
    conn = duckdb.connect()
    try:
        conn.execute(
            """
            CREATE TABLE earnings_events (
                ticker VARCHAR, earnings_dt DATE, timing VARCHAR,
                fiscal_year INTEGER, fiscal_q VARCHAR,
                eps_actual DOUBLE, eps_estimate DOUBLE,
                revenue_actual DOUBLE, revenue_estimate DOUBLE,
                source VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO earnings_events VALUES (
                'ACME', '2026-07-30', 'after_market_close', 2026, 'Q2',
                NULL, NULL, NULL, NULL, 'test'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE v_eligible_straddles (
                ticker VARCHAR, as_of_date DATE, expiry_date DATE, dte INTEGER,
                atm_strike DOUBLE, atm_iv DOUBLE,
                call_iv DOUBLE, put_iv DOUBLE,
                straddle_mid DOUBLE, straddle_pct DOUBLE,
                call_delta DOUBLE, call_gamma DOUBLE,
                call_vega DOUBLE, call_theta DOUBLE,
                quote_quality_status VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO v_eligible_straddles VALUES (
                'ACME', '2026-06-03', '2026-06-17', 14,
                100, .30, .31, .29, 6, .06,
                .5, .02, .1, -.1, 'decision_eligible_eod'
            )
            """
        )

        payload = build_symbol_detail(
            conn,
            "ACME",
            date(2026, 6, 3),
            date(2026, 7, 30),
        )
    finally:
        conn.close()

    assert payload is not None
    assert payload["straddle_features"][0]["expiration"] == "2026-06-17"
    assert payload["expected_move"] is None
