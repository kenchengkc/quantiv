from __future__ import annotations

import copy
import json
from datetime import date

import duckdb
import pytest

from frontend_data.display_forecast import DisplayForecastError
from frontend_data import display_payloads
from frontend_data.display_payloads import (
    build_display_forecast_status,
    enrich_upcoming_event,
    ensure_symbol_display_detail,
    validate_upcoming_display_forecasts,
)


def _payload(event: dict) -> dict:
    return {
        "window": {"start": "2026-09-21", "end": "2026-09-25"},
        "events": [event],
    }


def _indicative_event() -> dict:
    return {
        "ticker": "PAYX",
        "earnings_date": "2026-09-23",
        "display_forecast_pct": 0.083,
        "display_forecast_method": "options_indicative",
        "display_forecast_as_of": "2026-09-14",
        "ml_status": "unavailable_inputs",
        "options_status": "indicative",
        "fallback_reason": "quote_quality",
        "historical_event_count": None,
    }


def test_upcoming_published_event_requires_finite_display_forecast():
    published = [("PAYX", date(2026, 9, 23), "bmo")]
    week = {date(2026, 9, 21): _payload(_indicative_event())}
    validate_upcoming_display_forecasts(published, week, today=date(2026, 9, 16))


def test_enrichment_preserves_ml_snapshot_date_as_display_provenance():
    conn = duckdb.connect()
    event = {
        "ticker": "AIR",
        "earnings_date": "2026-09-21",
        "timing": "unknown",
        "em_ml_pct": 0.07,
        "ml_snapshot_date": "2026-09-09",
    }

    enrich_upcoming_event(
        conn,
        event,
        as_of_date=date(2026, 9, 14),
        today=date(2026, 9, 16),
    )

    assert event["display_forecast_method"] == "ml"
    assert event["display_forecast_as_of"] == "2026-09-09"


def test_enrichment_prefers_ml_even_when_strict_iv_is_available():
    conn = duckdb.connect()
    event = {
        "ticker": "GIS",
        "earnings_date": "2026-09-23",
        "timing": "bmo",
        "em_ml_pct": 0.043,
        "ml_snapshot_date": "2026-09-16",
        "em_straddle_pct": 0.0867,
        "em_iv_pct": 0.1052,
        "expiry_date": "2026-10-16",
        "days_to_expiry": 28,
        "atm_iv": 0.3797,
    }

    enrich_upcoming_event(
        conn,
        event,
        as_of_date=date(2026, 9, 18),
        today=date(2026, 9, 19),
    )

    assert event["display_forecast_method"] == "ml"
    assert event["display_forecast_pct"] == pytest.approx(0.043)
    assert event["ml_status"] == "available"
    assert event["options_status"] == "decision_eligible"

    validate_upcoming_display_forecasts(
        [("GIS", date(2026, 9, 23), "bmo")],
        {date(2026, 9, 21): _payload(event)},
        today=date(2026, 9, 19),
    )


def test_upcoming_published_event_with_dash_state_fails_closed():
    published = [("PAYX", date(2026, 9, 23), "bmo")]
    event = _indicative_event()
    event["display_forecast_pct"] = None
    event["display_forecast_method"] = None
    week = {date(2026, 9, 21): _payload(event)}
    with pytest.raises(DisplayForecastError, match="display forecast invariant failed"):
        validate_upcoming_display_forecasts(published, week, today=date(2026, 9, 16))


def test_upcoming_published_event_with_incoherent_options_status_fails_closed():
    published = [("PAYX", date(2026, 9, 23), "bmo")]
    event = _indicative_event()
    event["options_status"] = "unavailable"
    week = {date(2026, 9, 21): _payload(event)}
    with pytest.raises(DisplayForecastError, match="indicative options status"):
        validate_upcoming_display_forecasts(published, week, today=date(2026, 9, 16))


def test_upcoming_published_event_with_incoherent_historical_prior_fails_closed():
    published = [("NEW", date(2026, 9, 23), "bmo")]
    event = {
        "ticker": "NEW",
        "earnings_date": "2026-09-23",
        "display_forecast_pct": 0.055,
        "display_forecast_method": "historical_prior",
        "display_forecast_as_of": "2026-09-23",
        "ml_status": "unavailable_inputs",
        "options_status": "unavailable",
        "fallback_reason": "quote_quality",
        "historical_event_count": 1,
    }
    week = {date(2026, 9, 21): _payload(event)}
    with pytest.raises(DisplayForecastError, match="insufficient_ticker_history"):
        validate_upcoming_display_forecasts(published, week, today=date(2026, 9, 16))


def test_display_status_tracks_method_mix_separately_from_research_coverage():
    published = [
        ("AIR", date(2026, 9, 21), "unknown"),
        ("PAYX", date(2026, 9, 23), "bmo"),
    ]
    week = {
        date(2026, 9, 21): {
            "window": {"start": "2026-09-21", "end": "2026-09-25"},
            "events": [
                {
                    "ticker": "AIR",
                    "earnings_date": "2026-09-21",
                    "display_forecast_pct": 0.07,
                    "display_forecast_method": "ml",
                },
                {
                    "ticker": "PAYX",
                    "earnings_date": "2026-09-23",
                    "display_forecast_pct": 0.083,
                    "display_forecast_method": "options_indicative",
                },
            ],
        }
    }
    status = build_display_forecast_status(
        published,
        week,
        today=date(2026, 9, 16),
        generated_at="2026-09-16T15:00:00Z",
    )
    assert status["coverage_pct"] == 1.0
    assert status["method_mix"]["ml"] == 1
    assert status["method_mix"]["options_indicative"] == 1


def _history_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE earnings_events (
            ticker VARCHAR, earnings_dt DATE, timing VARCHAR,
            fiscal_year INTEGER, fiscal_q VARCHAR, eps_actual DOUBLE,
            eps_estimate DOUBLE, revenue_actual DOUBLE, revenue_estimate DOUBLE,
            source VARCHAR, timing_source VARCHAR
        )
    """)
    conn.execute("CREATE TABLE v_ohlcv (date DATE, act_symbol VARCHAR, close DOUBLE)")
    conn.execute("""
        CREATE VIEW v_corporate_action_coverage AS
        SELECT DISTINCT ticker AS act_symbol, DATE '2023-01-01' AS window_start,
               DATE '2028-12-31' AS window_end FROM earnings_events
    """)
    return conn


def _history_detail(conn, *, as_of_date=date(2026, 9, 16)):
    return ensure_symbol_display_detail(
        conn,
        None,
        ticker="A",
        as_of_date=as_of_date,
        earnings_date=date(2026, 10, 15),
        display_event={
            "display_forecast_pct": 0.05,
            "display_forecast_method": "historical",
            "display_forecast_as_of": as_of_date.isoformat(),
            "ml_status": "unavailable_inputs",
            "options_status": "unavailable",
            "fallback_reason": "no_event_expiry",
            "historical_event_count": 2,
        },
    )


def test_optionless_symbol_history_preserves_signed_exact_adjusted_actuals():
    conn = _history_conn()
    conn.executemany("""
        INSERT INTO earnings_events VALUES ('A', ?, ?, 2026, ?, NULL, NULL,
            NULL, NULL, 'finnhub', 'reported')
    """, [(date(2026, 9, 15), "bmo", "Q3"), (date(2026, 6, 15), "amc", "Q2")])
    conn.executemany(
        "INSERT INTO v_ohlcv VALUES (?, 'A', ?)",
        [(date(2026, 9, 14), 100.0), (date(2026, 9, 15), 52.0),
         (date(2026, 6, 15), 100.0), (date(2026, 6, 16), 96.0)],
    )
    conn.execute("""
        CREATE TABLE v_splits AS SELECT 'A' AS act_symbol,
            DATE '2026-09-15' AS ex_date, 2.0 AS to_factor, 1.0 AS for_factor
    """)
    conn.execute("""
        CREATE TABLE v_dividends AS SELECT 'A' AS act_symbol,
            DATE '2026-09-15' AS ex_date, 1.0 AS amount
    """)

    detail = _history_detail(conn)

    assert detail is not None
    history = detail["earnings_history"]
    assert [row["actual"] for row in history] == pytest.approx([0.06, -0.04])
    assert all(row["realized_target_protocol"] == "quantiv.session-reaction.v2" for row in history)
    assert all(row["realized_label_source"] == "ohlcv_session_close" for row in history)


@pytest.mark.parametrize("timing,timing_source", [
    ("unknown", "reported"), ("dmh", "reported"), ("bmo", "inferred"),
])
def test_optionless_symbol_history_withholds_unverified_timing(timing, timing_source):
    conn = _history_conn()
    conn.execute("""
        INSERT INTO earnings_events VALUES ('A', DATE '2026-09-15', ?, 2026, 'Q3',
            NULL, NULL, NULL, NULL, 'finnhub', ?)
    """, [timing, timing_source])
    conn.executemany("INSERT INTO v_ohlcv VALUES (?, 'A', ?)",
                     [(date(2026, 9, 14), 100.0), (date(2026, 9, 15), 110.0),
                      (date(2026, 9, 16), 120.0)])

    assert _history_detail(conn)["earnings_history"][0]["actual"] is None


def test_optionless_symbol_history_requires_mature_exact_session_prices():
    conn = _history_conn()
    conn.execute("""
        INSERT INTO earnings_events VALUES ('A', DATE '2026-09-15', 'amc', 2026, 'Q3',
            NULL, NULL, NULL, NULL, 'finnhub', 'reported')
    """)
    conn.executemany("INSERT INTO v_ohlcv VALUES (?, 'A', ?)",
                     [(date(2026, 9, 15), 100.0), (date(2026, 9, 17), 120.0)])
    assert _history_detail(conn)["earnings_history"][0]["actual"] is None

    conn.execute("INSERT INTO v_ohlcv VALUES (DATE '2026-09-16', 'A', 110.0)")
    assert _history_detail(conn, as_of_date=date(2026, 9, 15))["earnings_history"][0]["actual"] is None
    assert _history_detail(conn)["earnings_history"][0]["actual"] == pytest.approx(0.10)


def test_optionless_symbol_history_requires_action_coverage():
    conn = _history_conn()
    conn.execute("""
        INSERT INTO earnings_events VALUES ('A', DATE '2026-09-15', 'bmo', 2026, 'Q3',
            NULL, NULL, NULL, NULL, 'finnhub', 'reported')
    """)
    conn.executemany("INSERT INTO v_ohlcv VALUES (?, 'A', ?)",
                     [(date(2026, 9, 14), 100.0), (date(2026, 9, 15), 110.0)])
    conn.execute("""
        CREATE OR REPLACE VIEW v_corporate_action_coverage AS
        SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS window_start,
               NULL::DATE AS window_end WHERE FALSE
    """)
    assert _history_detail(conn)["earnings_history"][0]["actual"] is None


def test_optionless_symbol_history_does_not_publish_nonfinite_actuals():
    conn = _history_conn()
    conn.execute("""
        INSERT INTO earnings_events VALUES ('A', DATE '2026-09-15', 'bmo', 2026, 'Q3',
            NULL, NULL, NULL, NULL, 'finnhub', 'reported')
    """)
    conn.executemany("INSERT INTO v_ohlcv VALUES (?, 'A', ?)",
                     [(date(2026, 9, 14), 100.0), (date(2026, 9, 15), float('inf'))])

    row = _history_detail(conn)["earnings_history"][0]

    assert row["actual"] is None
    assert row["realized_target_protocol"] is None
    assert row["realized_label_source"] is None


def _held_options_conn():
    conn = _history_conn()
    conn.executemany("""
        INSERT INTO earnings_events VALUES ('A', ?, 'amc', 2026, ?, NULL, NULL,
            NULL, NULL, 'finnhub', 'reported')
    """, [(date(2026, 4, 16), "Q1"), (date(2026, 7, 16), "Q2")])
    conn.executemany(
        "INSERT INTO v_ohlcv VALUES (?, 'A', ?)",
        [(date(2026, 4, 16), 100.0), (date(2026, 4, 17), 103.0),
         (date(2026, 7, 16), 100.0), (date(2026, 7, 17), 105.0)],
    )
    return conn


def _held_options_event(earnings_date="2026-09-23"):
    return {
        "ticker": "A",
        "earnings_date": earnings_date,
        "timing": "bmo",
        "as_of_date": "2026-09-16",
        "em_iv_pct": 0.20,
        "em_straddle_pct": 0.18,
        "em_straddle_abs": 18.0,
        "atm_iv": 0.40,
        "em_method": "options_math",
        "display_forecast_pct": 0.20,
        "display_forecast_method": "options_math",
        "display_forecast_as_of": "2026-09-16",
        "options_status": "decision_eligible",
    }


def test_held_options_enrichment_clears_legacy_inputs_and_passes_display_contract():
    conn = _held_options_conn()
    event = _held_options_event()

    enrich_upcoming_event(
        conn, event, as_of_date=date(2026, 9, 16), today=date(2026, 9, 17),
        options_available=False,
    )

    assert event["em_iv_pct"] is None
    assert event["em_straddle_pct"] is None
    assert event["em_straddle_abs"] is None
    assert event["atm_iv"] is None
    assert event["em_method"] == "historical"
    assert event["display_forecast_method"] == "historical"
    assert event["display_forecast_pct"] == pytest.approx(0.04)
    assert event["options_status"] == "unavailable"
    assert event["fallback_reason"] == "options_held"
    validate_upcoming_display_forecasts(
        [("A", date(2026, 9, 23), "bmo")],
        {date(2026, 9, 21): _payload(event)},
        today=date(2026, 9, 17),
    )


@pytest.mark.parametrize("earnings_date", ["2026-09-16", "2026-09-17"])
def test_held_options_enrichment_preserves_frozen_events(earnings_date):
    conn = _held_options_conn()
    event = _held_options_event(earnings_date)
    frozen = copy.deepcopy(event)

    enrich_upcoming_event(
        conn, event, as_of_date=date(2026, 9, 16), today=date(2026, 9, 17),
        options_available=False,
    )

    assert event == frozen


@pytest.mark.parametrize("publication_held", [False, True])
@pytest.mark.parametrize("retained_source_date", ["2026-09-16", "2026-09-01"])
def test_held_options_projection_suppresses_retained_surfaces_without_resurrection(
    tmp_path, publication_held, retained_source_date,
):
    from frontend_data.forecast_artifacts import withhold_upcoming_ml

    conn = _held_options_conn()
    event = _held_options_event()
    event.update(em_ml_pct=0.06, ml_snapshot_date="2026-09-15", as_of_date=retained_source_date)
    frozen = _held_options_event("2026-09-17")
    future_history = {
        "date": "2026-09-23", "timing": "bmo", "implied": 0.18,
        "implied_as_of": "2026-09-16", "implied_atm_iv": 0.40,
        "implied_dte": 30, "implied_quality_status": "decision_eligible_eod",
    }
    frozen_history = {**future_history, "date": "2026-09-17"}
    symbol = {
        "symbol": "A", "as_of_date": "2026-09-16",
        "expected_move": {
            **event, "iv_pct": 0.20, "straddle_pct": 0.18,
            "straddle_abs": 18.0,
        },
        "earnings_history": [
            future_history, frozen_history,
            {"date": "2026-07-16", "actual": 0.05},
            {"date": "2026-04-16", "actual": 0.03},
        ],
    }
    calendar = {"metadata": {"as_of_date": retained_source_date}, "events": [event, frozen]}
    for relative in ("weekly.json", "screener.json", "weeks/2026-09-21.json", "weeks/retained.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(calendar))
    symbol_path = tmp_path / "symbols/A.json"
    symbol_path.parent.mkdir()
    symbol_path.write_text(json.dumps(symbol))

    display_payloads.suppress_held_options(
        tmp_path, conn=conn, as_of_date=date(2026, 9, 16), today=date(2026, 9, 17),
        publication_held=publication_held,
    )
    if publication_held:
        withhold_upcoming_ml(tmp_path, today=date(2026, 9, 17))

    for relative in ("weekly.json", "screener.json", "weeks/2026-09-21.json", "weeks/retained.json"):
        events = json.loads((tmp_path / relative).read_text())["events"]
        assert events[0]["display_forecast_method"] == ("historical" if publication_held else "ml")
        assert events[0]["display_forecast_pct"] == pytest.approx(0.04 if publication_held else 0.06)
        assert events[0]["em_iv_pct"] is None
        assert events[0]["em_straddle_pct"] is None
        assert events[0]["options_status"] == "unavailable"
        assert events[0]["options_publication_status"] == "held"
        assert events[0]["options_publication_source_date"] == "2026-09-16"
        assert events[0]["as_of_date"] == retained_source_date
        assert events[1] == frozen
    rebuilt = json.loads(symbol_path.read_text())
    assert rebuilt["expected_move"]["iv_pct"] is None
    assert rebuilt["expected_move"]["straddle_pct"] is None
    assert rebuilt["as_of_date"] == "2026-09-16"
    assert rebuilt["expected_move"]["display_forecast_method"] == ("historical" if publication_held else "ml")
    assert rebuilt["earnings_history"][0]["implied"] is None
    assert rebuilt["earnings_history"][0]["implied_atm_iv"] is None
    assert rebuilt["earnings_history"][0]["implied_dte"] is None
    assert rebuilt["earnings_history"][0]["options_status"] == "unavailable"
    assert rebuilt["earnings_history"][1] == frozen_history


@pytest.mark.parametrize("mutation", [
    "unmarked", "different_cutoff", "different_display_date", "option_method",
])
def test_ml_hold_rejects_source_mismatch_without_matching_options_projection(tmp_path, mutation):
    from frontend_data.forecast_artifacts import withhold_upcoming_ml

    event = {
        "ticker": "A", "earnings_date": "2026-09-23", "as_of_date": "2026-09-01",
        "display_forecast_pct": 0.04, "display_forecast_method": "historical",
        "display_forecast_as_of": "2026-09-16", "historical_event_count": 2,
        "options_status": "unavailable", "fallback_reason": "options_held",
        "options_publication_status": "held", "options_publication_source_date": "2026-09-16",
    }
    expected = copy.deepcopy(event)
    if mutation == "unmarked":
        expected.pop("options_publication_status")
    elif mutation == "different_cutoff":
        expected["options_publication_source_date"] = "2026-09-15"
    elif mutation == "different_display_date":
        expected["display_forecast_as_of"] = "2026-09-15"
    else:
        for node in (event, expected):
            node["display_forecast_method"] = "options_indicative"
            node["fallback_reason"] = "quote_quality"
    (tmp_path / "weekly.json").write_text(json.dumps({"events": [event]}))
    (tmp_path / "symbols").mkdir()
    (tmp_path / "symbols/A.json").write_text(json.dumps({
        "symbol": "A", "as_of_date": "2026-09-16", "expected_move": expected,
    }))

    with pytest.raises(RuntimeError, match="incompatible source dates"):
        withhold_upcoming_ml(tmp_path, today=date(2026, 9, 17))


def test_ml_hold_preserves_held_prior_when_compact_history_crosses_projection_cutoff(tmp_path):
    from frontend_data.forecast_artifacts import withhold_upcoming_ml

    conn = _history_conn()
    conn.executemany("""
        INSERT INTO earnings_events VALUES (?, ?, 'amc', 2026, ?, NULL, NULL,
            NULL, NULL, 'finnhub', 'reported')
    """, [("A", date(2026, 7, 16), "Q2"),
          ("A", date(2026, 9, 17), "Q3"),
          ("B", date(2026, 4, 16), "Q1"), ("B", date(2026, 7, 16), "Q2")])
    conn.executemany("INSERT INTO v_ohlcv VALUES (?, ?, ?)", [
        (date(2026, 7, 16), "A", 100.0), (date(2026, 7, 17), "A", 105.0),
        (date(2026, 9, 17), "A", 100.0), (date(2026, 9, 18), "A", 107.0),
        (date(2026, 4, 16), "B", 100.0), (date(2026, 4, 17), "B", 109.0),
        (date(2026, 7, 16), "B", 100.0), (date(2026, 7, 17), "B", 111.0),
    ])
    event = _held_options_event()
    (tmp_path / "weekly.json").write_text(json.dumps({"events": [event]}))
    (tmp_path / "symbols").mkdir()
    (tmp_path / "symbols/A.json").write_text(json.dumps({
        "symbol": "A", "as_of_date": "2026-09-16", "expected_move": event,
        "earnings_history": [
            {"date": "2026-09-17", "actual": 0.07},
            {"date": "2026-07-16", "actual": 0.05},
        ],
    }))
    display_payloads.suppress_held_options(
        tmp_path, conn=conn, as_of_date=date(2026, 9, 16), today=date(2026, 9, 21),
        publication_held=True,
    )
    projected = json.loads((tmp_path / "symbols/A.json").read_text())["expected_move"]
    assert projected["display_forecast_method"] == "historical_prior"
    assert projected["display_forecast_pct"] == pytest.approx(0.09)

    withhold_upcoming_ml(tmp_path, today=date(2026, 9, 21))

    rebuilt = json.loads((tmp_path / "weekly.json").read_text())["events"][0]
    assert rebuilt["display_forecast_method"] == "historical_prior"
    assert rebuilt["display_forecast_pct"] == pytest.approx(0.09)
    assert rebuilt["display_forecast_as_of"] == "2026-09-16"
    assert rebuilt["historical_event_count"] == 1
    assert rebuilt["fallback_reason"] == "insufficient_ticker_history"
    assert rebuilt["options_status"] == "unavailable"


@pytest.mark.parametrize("method", ["historical", "historical_prior"])
def test_python_display_compatibility_keeps_validated_held_cutoff_ahead_of_legacy_inputs(method):
    from tools.validate_public_contracts import _resolved_forecast_signature

    fields = {
        "display_forecast_pct": 0.09, "display_forecast_method": method,
        "display_forecast_as_of": "2026-09-16", "iv_pct": 0.20,
        "options_status": "unavailable", "options_publication_status": "held",
        "options_publication_source_date": "2026-09-16",
    }

    assert _resolved_forecast_signature(fields, historical_fallback=0.06) == (method, 0.09)
    assert _resolved_forecast_signature({**fields, "em_ml_pct": 0.12}) == ("ml", 0.12)
    assert _resolved_forecast_signature({
        **fields, "options_publication_source_date": "2026-09-15",
    }, historical_fallback=0.06) == ("options_math", 0.20)
