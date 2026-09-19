"""Regression tests for carrying reported events across a rebuild.

An expected move is only observable before the print. Once an earnings date has
passed, `compute_em_math` returns None for it and a plain rebuild drops the row,
so the calendar renders the event with no forecast. These tests pin the rule that
reported events survive a rebuild while upcoming ones stay fail-closed.
"""

from __future__ import annotations

from datetime import date

import pytest

from frontend_data.payloads import attach_frozen_event_forecasts, preserve_reported_events


def _event(ticker: str, earnings_date: str, **extra) -> dict:
    return {"ticker": ticker, "earnings_date": earnings_date, **extra}


def test_reported_events_survive_a_midweek_rebuild():
    """The bug: Mon/Tue reporters eroded out of the current week's bundle.

    Rebuilding on Thursday can no longer price Tuesday's print, so the fresh
    build only contains Thursday's event. The Tuesday row has to be retained
    with its forecast instead of silently disappearing.
    """
    fresh = [_event("ADBE", "2026-09-10", em_straddle_pct=0.108)]
    prior = [
        _event("CASY", "2026-09-08", em_straddle_pct=0.061),
        _event("CHWY", "2026-09-09", em_ml_pct=0.094),
        _event("ADBE", "2026-09-10", em_straddle_pct=0.108),
    ]

    merged = preserve_reported_events(fresh, prior, date(2026, 9, 10))

    assert [e["ticker"] for e in merged] == ["CASY", "CHWY", "ADBE"]
    assert merged[0]["em_straddle_pct"] == 0.061
    assert merged[1]["em_ml_pct"] == 0.094


def test_upcoming_events_stay_fail_closed():
    """A still-upcoming event that the fresh build declined to publish must not
    be resurrected from yesterday's bundle — absence of current evidence is the
    honest state for an event that has not reported yet."""
    fresh: list[dict] = []
    prior = [_event("HUBG", "2026-09-17", em_straddle_pct=0.052)]

    assert preserve_reported_events(fresh, prior, date(2026, 9, 14)) == []


def test_event_reporting_today_is_retained():
    """An event dated today has already been priced at least once, so it is
    retained rather than dropped mid-session."""
    prior = [_event("KR", "2026-09-11", em_ml_pct=0.047)]

    merged = preserve_reported_events([], prior, date(2026, 9, 11))

    assert [e["ticker"] for e in merged] == ["KR"]


def test_fresh_rows_win_over_retained_rows():
    """A late EPS actual or corrected realized move landing in the fresh build
    replaces the retained copy instead of duplicating the event."""
    fresh = [_event("ADBE", "2026-09-10", realized_move_pct=0.0137, eps_actual=5.31)]
    prior = [_event("ADBE", "2026-09-10", realized_move_pct=None, eps_actual=None)]

    merged = preserve_reported_events(fresh, prior, date(2026, 9, 14))

    assert len(merged) == 1
    assert merged[0]["eps_actual"] == 5.31
    assert merged[0]["realized_move_pct"] == 0.0137


def test_non_canonical_dates_are_not_resurrected():
    """A revised date the dedup collapsed away must not come back as a
    duplicate of the canonical event."""
    canonical = {("PLUS", "2026-09-09")}
    fresh = [_event("PLUS", "2026-09-09", em_straddle_pct=0.07)]
    prior = [
        _event("PLUS", "2026-09-08", em_straddle_pct=0.07),
        _event("PLUS", "2026-09-09", em_straddle_pct=0.07),
    ]

    merged = preserve_reported_events(fresh, prior, date(2026, 9, 14), canonical)

    assert [(e["ticker"], e["earnings_date"]) for e in merged] == [("PLUS", "2026-09-09")]


def test_output_stays_sorted_by_date_then_ticker():
    fresh = [_event("ZTS", "2026-09-09")]
    prior = [_event("ABM", "2026-09-08"), _event("AEO", "2026-09-09")]

    merged = preserve_reported_events(fresh, prior, date(2026, 9, 14))

    assert [(e["earnings_date"], e["ticker"]) for e in merged] == [
        ("2026-09-08", "ABM"),
        ("2026-09-09", "AEO"),
        ("2026-09-09", "ZTS"),
    ]


def test_fresh_reported_row_keeps_prior_ml_forecast_but_refreshes_outcome():
    fresh = [
        _event(
            "MU",
            "2026-09-16",
            em_ml_pct=None,
            display_forecast_pct=None,
            realized_move_pct=-0.071,
            eps_actual=2.91,
        )
    ]
    prior = [
        _event(
            "MU",
            "2026-09-16",
            em_ml_pct=0.083,
            model_horizon=1,
            ml_snapshot_date="2026-09-15",
            display_forecast_pct=0.083,
            display_forecast_method="ml",
            realized_move_pct=None,
            eps_actual=None,
        )
    ]

    merged = preserve_reported_events(fresh, prior, date(2026, 9, 17))

    assert len(merged) == 1
    event = merged[0]
    assert event["em_ml_pct"] == 0.083
    assert event["display_forecast_pct"] == 0.083
    assert event["display_forecast_method"] == "ml"
    assert event["ml_snapshot_date"] == "2026-09-15"
    assert event["realized_move_pct"] == -0.071
    assert event["eps_actual"] == 2.91



def test_frozen_event_archive_populates_reported_hero_and_history():
    detail = {
        "expected_move": None,
        "earnings_history": [
            {
                "date": "2026-09-16",
                "timing": "after_market_close",
                "actual": -0.071,
                "eps_actual": 2.91,
            }
        ],
    }
    archive = {
        ("MU", "2026-09-16"): {
            "act_symbol": "MU",
            "earnings_date": date(2026, 9, 16),
            "snapshot_date": date(2026, 9, 15),
            "model_horizon": 1,
            "em_ml_pct": 0.083,
            "em_ml_abs": 9.12,
            "p10": 0.03,
            "p25": 0.05,
            "p50": 0.08,
            "p75": 0.11,
            "p90": 0.14,
        }
    }

    result = attach_frozen_event_forecasts(
        detail,
        "MU",
        archive,
        current_event_date=date(2026, 9, 16),
        today=date(2026, 9, 17),
    )

    assert result["expected_move"]["display_forecast_method"] == "ml"
    assert result["expected_move"]["display_forecast_pct"] == 0.083
    assert result["expected_move"]["forecast_frozen"] is True
    assert result["earnings_history"][0]["em_ml_pct"] == 0.083
    assert result["earnings_history"][0]["ml_snapshot_date"] == "2026-09-15"
    assert result["earnings_history"][0]["forecast_frozen"] is True


def test_upcoming_event_does_not_replace_current_expected_move_with_archive():
    detail = {
        "expected_move": {
            "earnings_date": "2026-09-25",
            "em_ml_pct": 0.06,
        },
        "earnings_history": [],
    }
    archive = {
        ("MU", "2026-09-25"): {
            "act_symbol": "MU",
            "earnings_date": date(2026, 9, 25),
            "snapshot_date": date(2026, 9, 18),
            "model_horizon": 7,
            "em_ml_pct": 0.05,
        }
    }

    result = attach_frozen_event_forecasts(
        detail,
        "MU",
        archive,
        current_event_date=date(2026, 9, 25),
        today=date(2026, 9, 19),
    )

    assert result["expected_move"]["em_ml_pct"] == 0.06
    assert result["expected_move"].get("forecast_frozen") is None



def test_reported_symbol_uses_iv_forecast_before_historical_median():
    detail = {
        "expected_move": None,
        "earnings_history": [
            {
                "date": "2026-09-09",
                "timing": "after_market_close",
                "actual": -0.14666,
                "implied": 0.081714,
                "implied_as_of": "2026-09-09",
                "implied_expiration": "2026-09-18",
                "implied_dte": 9,
                "implied_lead_days": 0,
                "implied_atm_strike": 70.0,
                "implied_straddle_abs": 5.72,
                "implied_atm_iv": 0.62145,
                "implied_quality_status": "decision_eligible_eod",
            }
        ],
    }

    result = attach_frozen_event_forecasts(
        detail,
        "COO",
        {},
        current_event_date=date(2026, 9, 9),
        today=date(2026, 9, 10),
    )

    expected = result["expected_move"]
    assert expected["display_forecast_method"] == "options_math"
    assert expected["display_forecast_pct"] == pytest.approx(0.62145 * (9 / 365.0) ** 0.5)
    assert expected["display_forecast_as_of"] == "2026-09-09"
    assert expected["options_status"] == "decision_eligible"
    assert expected["forecast_frozen"] is True


def test_historical_iv_wins_over_archived_ml_but_ml_is_preserved():
    detail = {
        "expected_move": None,
        "earnings_history": [
            {
                "date": "2026-09-16",
                "timing": "after_market_close",
                "actual": 0.013,
                "implied": 0.0498,
                "implied_as_of": "2026-09-16",
                "implied_dte": 16,
                "implied_atm_iv": 0.29635,
                "implied_quality_status": "decision_eligible_eod",
            }
        ],
    }
    archive = {
        ("FDX", "2026-09-16"): {
            "act_symbol": "FDX",
            "earnings_date": date(2026, 9, 16),
            "snapshot_date": date(2026, 9, 14),
            "model_horizon": 2,
            "em_ml_pct": 0.036898,
        }
    }

    result = attach_frozen_event_forecasts(
        detail,
        "FDX",
        archive,
        current_event_date=date(2026, 9, 16),
        today=date(2026, 9, 17),
    )

    assert result["expected_move"]["display_forecast_method"] == "options_math"
    assert result["expected_move"]["display_forecast_pct"] == pytest.approx(
        0.29635 * (16 / 365.0) ** 0.5
    )
    assert result["expected_move"]["em_ml_pct"] == 0.036898
    assert result["expected_move"]["ml_status"] == "available"
