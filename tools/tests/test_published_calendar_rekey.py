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

from frontend_data.payloads import load_published_calendar_keys, ml_gate_drops_event


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
