"""A published calendar date must still be priced after a provider revision.

daily_score.py keys ML to the date it scored. When that date moves, requiring
ML used to drop the event, so the homepage rendered the calendar identity with
no expected move. Published identities fall through to options math (or a
realized-only row) instead of disappearing.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from frontend_data.payloads import (
    align_symbol_detail_to_published,
    apply_published_symbol_dates,
    load_published_calendar_events,
    load_published_calendar_keys,
    ml_gate_drops_event,
    published_symbol_dates,
)


TODAY = date(2026, 9, 15)
ML = {("CNXC", "2026-09-24"): {"em_ml_pct": 0.0878}}


def test_published_revised_date_is_not_dropped_for_missing_ml():
    """CNXC's calendar date moved Sep 24 → 29; the forecast stayed on the 24th."""
    assert ml_gate_drops_event("CNXC", "2026-09-29", TODAY, ML, True, {("CNXC", "2026-09-29")}) is False


def test_unscored_unpublished_event_still_fails_closed():
    assert ml_gate_drops_event("ZZZZ", "2026-09-29", TODAY, ML, True, {("CNXC", "2026-09-29")}) is True


def test_matching_ml_key_is_never_dropped():
    assert ml_gate_drops_event("CNXC", "2026-09-24", TODAY, ML, True) is False


def test_past_events_do_not_require_ml():
    assert ml_gate_drops_event("KFY", "2026-09-09", TODAY, {}, True) is False


def test_require_ml_false_never_drops():
    assert ml_gate_drops_event("ZZZZ", "2026-09-29", TODAY, {}, False) is False


def test_load_published_calendar_keys_reads_identities(tmp_path: Path):
    (tmp_path / "calendar-reference.json").write_text(
        json.dumps({
            "events": [
                {"ticker": "cnxc", "earnings_date": "2026-09-29", "timing": "amc"},
                {"ticker": "HUBG", "earnings_date": "2026-09-17", "timing": "unknown"},
                {"ticker": "", "earnings_date": "2026-09-18"},
            ]
        }),
        encoding="utf-8",
    )

    assert load_published_calendar_keys(tmp_path) == {
        ("CNXC", "2026-09-29"),
        ("HUBG", "2026-09-17"),
    }


def test_load_published_calendar_keys_missing_file_is_empty(tmp_path: Path):
    assert load_published_calendar_keys(tmp_path) == set()


def test_published_symbol_dates_prefers_upcoming_print():
    events = [
        ("HUBG", date(2026, 8, 27), "unknown"),
        ("HUBG", date(2026, 9, 17), "unknown"),
        ("PRGS", date(2026, 6, 30), "unknown"),
        ("PRGS", date(2026, 9, 29), "amc"),
        ("FIZZ", date(2026, 9, 9), "bmo"),
    ]
    got = published_symbol_dates(events, TODAY)
    assert got["HUBG"] == (date(2026, 9, 17), "unknown")
    assert got["PRGS"] == (date(2026, 9, 29), "amc")
    assert got["FIZZ"] == (date(2026, 9, 9), "bmo")


def test_apply_published_symbol_dates_overwrites_stale_research():
    tickers = {"HUBG": date(2026, 8, 27), "AAPL": date(2026, 10, 1)}
    apply_published_symbol_dates(
        tickers,
        {"HUBG": (date(2026, 9, 17), "unknown")},
    )
    assert tickers["HUBG"] == date(2026, 9, 17)
    assert tickers["AAPL"] == date(2026, 10, 1)


def test_align_symbol_detail_drops_mismatched_expected_move():
    detail = {
        "next_earnings": "2026-08-27",
        "next_earnings_timing": "unknown",
        "expected_move": {"earnings_date": "2026-08-27", "straddle_pct": 0.08},
    }
    align_symbol_detail_to_published(detail, date(2026, 9, 17), "unknown")
    assert detail["next_earnings"] == "2026-09-17"
    assert detail["expected_move"] is None


def test_align_symbol_detail_keeps_matching_expected_move():
    detail = {
        "next_earnings": "2026-09-17",
        "expected_move": {"earnings_date": "2026-09-17", "straddle_pct": 0.08},
    }
    align_symbol_detail_to_published(detail, date(2026, 9, 17), "amc")
    assert detail["expected_move"]["earnings_date"] == "2026-09-17"
    assert detail["expected_move"]["timing"] == "amc"
    assert detail["next_earnings_timing"] == "amc"


def test_load_published_calendar_events_skips_bad_rows(tmp_path: Path):
    (tmp_path / "calendar-reference.json").write_text(
        json.dumps({
            "events": [
                {"ticker": "HUBG", "earnings_date": "2026-09-17", "timing": "unknown"},
                {"ticker": "BAD", "earnings_date": "not-a-date"},
            ]
        }),
        encoding="utf-8",
    )
    assert load_published_calendar_events(tmp_path) == [
        ("HUBG", date(2026, 9, 17), "unknown"),
    ]
