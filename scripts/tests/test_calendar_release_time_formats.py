"""Explicit release times with wire datelines and parenthesized timezones."""

from datetime import date

import pytest

from reconcile_earnings_calendar import extract_earnings_announcement


def test_globenewswire_dateline_preserves_release_date_and_session() -> None:
    parsed = extract_earnings_announcement(
        "Example Schedules Earnings Release. CITY, Oct. 8, 2026 (GLOBE NEWSWIRE) -- "
        "Example announced it will issue its third quarter earnings release after "
        "the market closes on November 4, 2026. Example will host a conference call "
        "on November 5, 2026 at 8:00 a.m. (ET).",
        published_on=date(2026, 10, 8),
    )

    assert parsed == {"date": "2026-11-04", "timing": "amc"}


@pytest.mark.parametrize("clock, expected", [
    ("after 5:00 p.m. (ET)", "amc"),
    ("before 8:00 a.m. (ET)", "bmo"),
    ("at 5:00 p.m. (ET)", "amc"),
    ("at 8:00 a.m. (Eastern Time)", "bmo"),
    ("before 5:00 p.m. (ET)", "unknown"),
    ("after 7:00 a.m. (ET)", "unknown"),
    ("not before 7:00 a.m. (ET)", "unknown"),
    ("not after 5:00 p.m. (ET)", "unknown"),
    ("by 5:00 p.m. (ET)", "unknown"),
    ("no later than 5:00 p.m. (ET)", "unknown"),
    ("no earlier than 6:00 a.m. (ET)", "unknown"),
    ("not until 6:00 a.m. (ET)", "unknown"),
    ("at 8:00 a.m. (UTC)", "unknown"),
])
def test_parenthesized_timezone_respects_explicit_release_bound(
    clock: str, expected: str,
) -> None:
    parsed = extract_earnings_announcement(
        f"Example expects to release its third quarter results {clock} on November 3, 2026. "
        "Example will hold a call at 11:30 a.m. (ET) on November 4, 2026.",
        published_on=date(2026, 10, 13),
    )

    assert parsed == {"date": "2026-11-03", "timing": expected}


@pytest.mark.parametrize("detail", [
    "Example will hold a conference call at 8:00 a.m. (ET) on November 3, 2026.",
    "Example will post a conference call replay at 5:00 p.m. (ET) on November 3, 2026.",
    "Example will issue fourth quarter results at 5:00 p.m. (ET) on February 2, 2027.",
    "Example will issue fourth quarter results on February 2, 2027 (GLOBE NEWSWIRE) -- after market close.",
])
def test_parenthesized_time_or_wire_marker_does_not_supply_unrelated_session(detail: str) -> None:
    parsed = extract_earnings_announcement(
        "Example will release third quarter results on November 3, 2026. " + detail,
        published_on=date(2026, 10, 13),
    )

    assert parsed == {"date": "2026-11-03", "timing": "unknown"}


@pytest.mark.parametrize("clock", ["before 5:00 p.m. ET", "after 7:00 a.m. ET"])
def test_release_clock_bound_cannot_cross_the_market_session(clock: str) -> None:
    parsed = extract_earnings_announcement(
        f"Example will release financial results {clock} on November 3, 2026.",
        published_on=date(2026, 10, 13),
    )

    assert parsed == {"date": "2026-11-03", "timing": "unknown"}


def test_explicit_release_clock_precedes_coordinated_call_clause() -> None:
    parsed = extract_earnings_announcement(
        "Example Announces Earnings Date. Example will release third quarter financial "
        "results at approximately 6:00 a.m. ET on October 27, 2026, and host a conference "
        "call at 8:30 a.m. ET to review the results.",
        published_on=date(2026, 10, 1),
    )

    assert parsed == {"date": "2026-10-27", "timing": "bmo"}


@pytest.mark.parametrize("dateline", [
    "", "CITY, Oct. 1, 2026 /PRNewswire/ -- ",
    "CITY, Oct. 1, 2026 (GLOBE NEWSWIRE) -- ",
])
@pytest.mark.parametrize("release", [
    "Example plans to release its third quarter financial results",
    "Results will be released",
])
def test_release_explicitly_precedes_same_day_morning_call(
    dateline: str, release: str,
) -> None:
    parsed = extract_earnings_announcement(
        "Example Announces Earnings Date. " + dateline + release
        + " on October 30, 2026, prior to the Company’s earnings conference call, "
        "which will be held at 8:30 a.m. ET on the same day.",
        published_on=date(2026, 10, 1),
    )

    assert parsed == {"date": "2026-10-30", "timing": "bmo"}


@pytest.mark.parametrize("detail", [
    "prior to the Company’s earnings conference call at 5:00 p.m. ET on the same day",
    "prior to the Company’s earnings conference call at 8:30 a.m. ET on October 31, 2026",
    "prior to the Company’s earnings conference call at 8:30 a.m. ET the following day",
    "the evening before the Company’s earnings conference call at 8:30 a.m. ET",
    "and host a conference call at 8:30 a.m. ET to discuss the results",
])
def test_active_release_does_not_gain_unsupported_session_from_call(detail: str) -> None:
    parsed = extract_earnings_announcement(
        "Example plans to release its financial results on October 30, 2026 " + detail + ".",
        published_on=date(2026, 10, 1),
    )

    assert parsed == {"date": "2026-10-30", "timing": "unknown"}


@pytest.mark.parametrize("filler", ["", "Registration details are available online. " * 10])
def test_conflicting_explicit_release_clocks_keep_session_unknown(filler: str) -> None:
    parsed = extract_earnings_announcement(
        "Example will release financial results on November 3, 2026 at 8:00 a.m. (ET). "
        + filler + "Example will release financial results on November 3, 2026 at 5:00 p.m. (ET).",
        published_on=date(2026, 10, 13),
    )

    assert parsed == {"date": "2026-11-03", "timing": "unknown"}
