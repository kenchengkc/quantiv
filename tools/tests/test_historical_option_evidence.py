from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from build_earnings_events import create_duckdb_views
from frontend_data.payloads import (
    _historical_option_evidence,
    build_symbol_detail,
    enrich_reported_event_forecasts,
)
from math_baseline import _is_pre_event_observation


def test_eod_observation_must_precede_the_event_session() -> None:
    event = date(2026, 2, 10)

    assert _is_pre_event_observation(date(2026, 2, 9), event, "before_market_open")
    assert not _is_pre_event_observation(event, event, "before_market_open")
    assert not _is_pre_event_observation(event, event, "after_market_close")
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
    assert evidence[date(2026, 5, 10)]["implied_as_of"] == "2026-05-09"
    assert evidence[date(2026, 5, 10)]["implied_lead_days"] == 1
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


@pytest.mark.parametrize(
    ("call_vega", "put_vega", "expected_iv_move"),
    [(0.0655, 0.0646, 0.138533), (0.1, 0.1, 0.138524),
     (0.0, 0.0646, 0.138524), (None, 0.0646, 0.138524)],
    ids=["INTC-unequal-vegas", "equal-vegas", "zero-vega", "missing-vega"],
)
def test_event_day_calendar_and_symbol_use_the_same_option_iv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    call_vega: float | None, put_vega: float | None, expected_iv_move: float,
) -> None:
    # The October 21 refresh mixed an arithmetic historical IV with a
    # vega-weighted current IV for this exact saved INTC pair.
    snapshot, event_day = date(2026, 10, 19), date(2026, 10, 21)
    rows = []
    for side, bid, ask, delta, iv, vega in [
        ("Call", 5.15, 5.25, 0.5298, 0.8056, call_vega),
        ("Put", 5.35, 5.45, -0.4991, 0.7903, put_vega),
    ]:
        row = _option("INTC", side, bid=bid, ask=ask, delta=delta)
        row.update(date=snapshot, expiration=date(2026, 10, 30), strike=94.0,
                   vol=iv, vega=vega, quote_timestamp=None)
        rows.append(row)
    partition = tmp_path / "data/parquet/options_chain/year=2026/month=10/quotes.parquet"
    partition.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(partition, index=False)

    event = {"ticker": "INTC", "earnings_date": event_day.isoformat(), "timing": "amc"}
    with duckdb.connect() as conn:
        create_duckdb_views(conn, tmp_path / "data")
        conn.execute("""CREATE TABLE earnings_events (
            ticker VARCHAR, earnings_dt DATE, timing VARCHAR,
            fiscal_year INTEGER, fiscal_q VARCHAR, eps_actual DOUBLE,
            eps_estimate DOUBLE, revenue_actual DOUBLE, revenue_estimate DOUBLE,
            source VARCHAR)""")
        conn.execute("INSERT INTO earnings_events VALUES ('INTC', ?, 'amc', 2026, 'Q3', NULL, NULL, NULL, NULL, 'test')", [event_day])
        enrich_reported_event_forecasts(conn, [event], today=event_day, archive={})
        symbol = build_symbol_detail(conn, "INTC", snapshot, event_day)

    assert symbol["expected_move"]["iv_pct"] == expected_iv_move
    assert event["em_iv_pct"] == expected_iv_move
    assert event["as_of_date"] == snapshot.isoformat()
    assert symbol["as_of_date"] == snapshot.isoformat()
    assert event["expiry_date"] == symbol["expected_move"]["expiration"] == "2026-10-30"

    # Exercise the same publication gate that stopped the real refresh.
    import validate_public_contracts as contracts
    public = tmp_path / "public"
    (public / "weeks").mkdir(parents=True)
    (public / "symbols").mkdir()
    (public / "weeks/2026-10-19.json").write_text(json.dumps({
        "metadata": {"as_of_date": snapshot.isoformat()}, "events": [event],
    }))
    (public / "symbols/INTC.json").write_text(json.dumps(symbol))
    monkeypatch.setattr(contracts, "PUBLIC", public)
    contracts.validate_forecast_surface_parity()


def test_historical_evidence_reuses_cached_pairs_and_filters_unrelated_symbols(
    tmp_path: Path,
) -> None:
    rows = [
        _option(symbol, side, bid=4.0, ask=4.2, delta=delta)
        for symbol in ("ACME", "OTHER")
        for side, delta in (("Call", 0.5), ("Put", -0.5))
    ]
    partition = tmp_path / "parquet/options_chain/year=2026/month=02/data.parquet"
    partition.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(partition, index=False)
    conn = duckdb.connect()

    class PlannedConnection:
        plan: list[dict]

        def execute(self, query, parameters):
            # Characterize the real bound query plan, rather than asserting SQL
            # spelling or timing a small fixture on shared CI hardware.
            self.plan = json.loads(
                conn.execute("EXPLAIN (FORMAT JSON) " + query, parameters).fetchone()[1]
            )
            return conn.execute(query, parameters)

    planned = PlannedConnection()
    try:
        create_duckdb_views(conn, tmp_path)
        conn.execute("CREATE TABLE earnings_events (ticker VARCHAR, earnings_dt DATE, timing VARCHAR)")
        conn.execute("INSERT INTO earnings_events VALUES ('ACME', '2026-02-11', 'before_market_open')")
        evidence = _historical_option_evidence(planned, "ACME", date(2026, 2, 12))
    finally:
        conn.close()

    def operators(nodes):
        for node in nodes:
            yield node
            yield from operators(node.get("children", []))

    nodes = list(operators(planned.plan))
    assert not any(node["name"].strip() == "READ_PARQUET" for node in nodes)
    scans = [node for node in nodes if str(node["extra_info"].get("Table", "")).endswith(
        "_frontend_cache_v_eligible_straddles"
    )]
    assert scans
    assert all("ticker='ACME'" in str(scan["extra_info"].get("Filters", "")) for scan in scans)
    assert list(evidence) == [date(2026, 2, 11)]
    assert evidence[date(2026, 2, 11)]["implied"] == 0.082
    assert evidence[date(2026, 2, 11)]["implied_quality_status"] == "decision_eligible_eod"


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
