from __future__ import annotations

from datetime import date

import duckdb
import pytest

from frontend_data.display_forecast import DisplayForecastError
from frontend_data.display_payloads import (
    build_display_forecast_status,
    enrich_upcoming_event,
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
