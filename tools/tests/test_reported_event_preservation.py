"""Regression tests for carrying reported events across a rebuild.

An expected move is only observable before the print. Once an earnings date has
passed, `compute_em_math` returns None for it and a plain rebuild drops the row,
so the calendar renders the event with no forecast. These tests pin the rule that
reported events survive a rebuild while upcoming ones stay fail-closed.
"""

from __future__ import annotations

from datetime import date

from frontend_data.payloads import preserve_reported_events


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
