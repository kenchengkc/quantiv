"""Explicit 24-hour release clocks retain timezone and event association guards."""

from datetime import date

import pytest

from reconcile_earnings_calendar import _session_from_text, extract_earnings_announcement


DAY = date(2026, 11, 6)


@pytest.mark.parametrize("clock,expected", [
    ("0:00 ET", "bmo"), ("00:00 ET", "bmo"), ("09:29 ET", "bmo"),
    ("09:30 ET", "unknown"), ("15:59 ET", "unknown"),
    ("16:00 ET", "amc"), ("23:59 ET", "amc"),
    ("06:00 Pacific Time", "bmo"), ("13:00 (Pacific Time)", "amc"),
    ("23:30 PT", "unknown"),
    ("12:00 a.m. ET", "bmo"), ("12:00 p.m. ET", "unknown"),
    ("4 p.m. ET", "amc"),
])
def test_release_clock_boundaries_and_existing_twelve_hour_behavior(clock, expected):
    assert _session_from_text(
        f"Example will release financial results on November 6, 2026 at {clock}.",
        report_day=DAY,
    ) == expected


@pytest.mark.parametrize("clock", [
    "24:00 ET", "25:00 ET", "07:60 ET", "07:00", "7 ET", "7:0 ET",
    "07:00 GST", "07:00 UTC", "00:00 p.m. ET", "13:00 a.m. ET",
    "17:00:00 ET", "25:07:00 ET", "07:00:00 p.m. ET", "123:07:00 ET",
])
def test_invalid_or_unqualified_clock_cannot_supply_a_session(clock):
    assert _session_from_text(
        f"Example will release financial results on November 6, 2026 at {clock}.",
        report_day=DAY,
    ) == "unknown"


@pytest.mark.parametrize("bound,clock,expected", [
    ("before", "09:00 ET", "bmo"),
    ("before", "17:00 ET", "unknown"),
    ("after", "08:00 ET", "unknown"),
    ("after", "16:00 ET", "amc"),
    ("no later than", "09:00 ET", "bmo"),
    ("no later than", "16:00 ET", "unknown"),
    ("no earlier than", "09:00 ET", "unknown"),
    ("no earlier than", "16:00 ET", "amc"),
    ("not before", "08:00 ET", "unknown"),
    ("not before", "16:00 ET", "amc"),
])
def test_twenty_four_hour_bounds_are_not_treated_as_exact_clocks(bound, clock, expected):
    assert _session_from_text(
        f"Example will release financial results on November 6, 2026 {bound} {clock}.",
        report_day=DAY,
    ) == expected


def test_dual_zone_release_clock_does_not_borrow_later_call_or_registration_clock():
    text = (
        "Example to Report 3Q26 Results on November 6, 2026. "
        "Example will release financial and operating results for the period ended "
        "September 30, 2026 at 9:00 GST / 0:00 ET on November 6, 2026. "
        "Management will host a results presentation and earnings call at 16:00 GST / 7:00 ET the same day. "
        "Starting on October 30, 2026 at 16:00 GST / 8:00 ET, the Q&A platform will "
        "become available for shareholders to submit questions."
    )
    assert extract_earnings_announcement(text, published_on=date(2026, 10, 8)) == {
        "date": "2026-11-06", "timing": "bmo",
    }


@pytest.mark.parametrize("text", [
    "Example will host its earnings conference call on November 6, 2026 at 07:00 ET.",
    "Example will release results on November 6, 2026 and host a call at 07:00 ET.",
    "Example will publish a replay of its earnings call on November 6, 2026 at 07:00 ET.",
    "Example will release results on November 5, 2026 at 07:00 ET. Its earnings call is November 6, 2026 at 17:00 ET.",
])
def test_call_or_different_event_clocks_do_not_supply_release_timing(text):
    assert _session_from_text(text, report_day=DAY) == "unknown"


@pytest.mark.parametrize("start", ["November 5, 2026", "Thursday, November 5, 2026"])
def test_en_dash_date_range_is_not_mistaken_for_publication_dateline(start):
    assert _session_from_text(
        f"{start} – November 6, 2026: Example will release financial results at 07:00 ET.",
        report_day=DAY,
    ) == "unknown"
