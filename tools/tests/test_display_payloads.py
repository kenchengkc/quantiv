from __future__ import annotations

from datetime import date

import pytest

from frontend_data.display_forecast import DisplayForecastError
from frontend_data.display_payloads import (
    build_display_forecast_status,
    validate_upcoming_display_forecasts,
)


def _payload(event: dict) -> dict:
    return {
        "window": {"start": "2026-09-21", "end": "2026-09-25"},
        "events": [event],
    }


def test_upcoming_published_event_requires_finite_display_forecast():
    published = [("PAYX", date(2026, 9, 23), "bmo")]
    week = {
        date(2026, 9, 21): _payload(
            {
                "ticker": "PAYX",
                "earnings_date": "2026-09-23",
                "display_forecast_pct": 0.083,
                "display_forecast_method": "options_indicative",
                "ml_status": "unavailable_inputs",
            }
        )
    }
    validate_upcoming_display_forecasts(published, week, today=date(2026, 9, 16))


def test_upcoming_published_event_with_dash_state_fails_closed():
    published = [("PAYX", date(2026, 9, 23), "bmo")]
    week = {
        date(2026, 9, 21): _payload(
            {
                "ticker": "PAYX",
                "earnings_date": "2026-09-23",
                "display_forecast_pct": None,
                "display_forecast_method": None,
                "ml_status": "unavailable_inputs",
            }
        )
    }
    with pytest.raises(DisplayForecastError, match="display forecast invariant failed"):
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
