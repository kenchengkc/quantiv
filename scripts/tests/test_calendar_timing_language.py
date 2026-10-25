"""Release-language regressions, using synthetic announcement text."""

from datetime import date

import pytest

from reconcile_earnings_calendar import (
    _announcement_from_article,
    _session_from_text,
    extract_earnings_announcement,
)


def test_sets_date_title_admits_an_explicit_earnings_release() -> None:
    announcement = _announcement_from_article(
        provider="test",
        title="Example Sets Date for Third Quarter 2026 Earnings Release and Conference Call",
        body="Example will release financial results on November 5, 2026 after market close.",
        published_on=date(2026, 10, 14),
        official=True,
        url=None,
    )

    assert announcement is not None
    assert (announcement["date"], announcement["timing"]) == ("2026-11-05", "amc")


def test_hyphenated_quarter_title_admits_explicit_release() -> None:
    announcement = _announcement_from_article(
        provider="test",
        title="Cincinnati Financial Schedules Webcast to Discuss Third-Quarter 2026 Results",
        body="Cincinnati Financial will release financial results on November 5, 2026 after market close.",
        published_on=date(2026, 10, 14),
        official=True,
        url=None,
    )

    assert announcement is not None
    assert (announcement["date"], announcement["timing"]) == ("2026-11-05", "amc")


def test_wire_dateline_and_next_day_call_preserve_explicit_release_session() -> None:
    parsed = extract_earnings_announcement(
        "CINCINNATI , Oct. 7, 2026 /PRNewswire/ -- Example Corporation plans to release "
        "its third-quarter 2026 results on Monday, October 26, 2026, after the close "
        "of regular trading on the Nasdaq Stock Market. The company will hold a "
        "conference call to discuss third-quarter 2026 results on Tuesday, October 27, "
        "at 11 a.m. ET.",
        published_on=date(2026, 10, 7),
    )

    assert parsed == {"date": "2026-10-26", "timing": "amc"}


def test_market_session_before_call_bound_still_times_the_explicit_release() -> None:
    # Same clause structure as INDI's October 14, 2026 announcement: the
    # after-close bound times the earnings release, not its later call.
    parsed = extract_earnings_announcement(
        "Example will hold a call on November 5, 2026 at 5 p.m. Eastern Time. "
        "After the close of the market on November 5 and prior to the conference call, "
        "Example will issue its earnings press release.",
        published_on=date(2026, 10, 14),
    )

    assert parsed == {"date": "2026-11-05", "timing": "amc"}


@pytest.mark.parametrize(
    "body",
    [
        "Example will hold its earnings conference call on November 5, 2026 after market close.",
        "Example will release financial results on November 5, 2026 and, after close, host a conference call.",
        "After market close on November 5, 2026, Example will host a call to discuss its financial results.",
        "After market close on November 5, 2026 and prior to the conference call, Example will issue its earnings call transcript.",
        "Example will release financial results on November 5, 2026 before its conference call at 5 p.m. Eastern Time.",
    ],
)
def test_call_timing_does_not_supply_an_earnings_release_session(body: str) -> None:
    parsed = extract_earnings_announcement(body, published_on=date(2026, 10, 14))

    assert parsed == {"date": "2026-11-05", "timing": "unknown"}


def test_explicit_release_before_same_day_morning_call_across_sentences() -> None:
    # AROC's announcement gives the call date, its clock, and the publication
    # bound in three consecutive sentences. These are synthetic equivalents.
    parsed = extract_earnings_announcement(
        "Example will host a conference call on November 3, 2026 to discuss quarterly results. "
        "The call will begin at 8:30 a.m. Eastern Time. "
        "Example will release its quarterly earnings report prior to the conference call.",
        published_on=date(2026, 10, 23),
    )

    assert parsed == {"date": "2026-11-03", "timing": "bmo"}


def test_call_bound_checks_conflicting_clock_outside_the_date_excerpt() -> None:
    parsed = extract_earnings_announcement(
        "Example will host a conference call on November 3, 2026 to discuss quarterly results. "
        "The call will begin at 8:30 a.m. Eastern Time. "
        "Example will release its quarterly earnings report prior to the conference call. "
        + "Participants can register through the investor website. " * 10
        + "Another call will begin at 5 p.m. ET.",
        published_on=date(2026, 10, 23),
    )

    assert parsed == {"date": "2026-11-03", "timing": "unknown"}


@pytest.mark.parametrize(
    "call_date, clock, release, extra",
    [
        ("November 3, 2026", "5:00 p.m. Eastern Time", "earnings report prior to the conference call", ""),
        ("November 4, 2026", "8:30 a.m. Eastern Time", "earnings report prior to the conference call", ""),
        ("November 3, 2026", "8:30 a.m. Eastern Time", "earnings report prior to the conference call", " Another call will begin at 5 p.m. ET."),
        ("November 3, 2026", "8:30 a.m. Eastern Time", "earnings call transcript prior to the conference call", ""),
        ("November 3, 2026", "8:30 a.m. Eastern Time", "earnings report replay prior to the conference call", ""),
        ("November 3, 2026", "8:30 a.m. Eastern Time", "earnings report after the conference call", ""),
        ("November 3, 2026", "8:30 a.m. Eastern Time", "earnings report on November 2, 2026 prior to the conference call", ""),
    ],
)
def test_ambiguous_or_nonrelease_cross_sentence_call_bound_stays_unknown(
    call_date: str, clock: str, release: str, extra: str,
) -> None:
    session = _session_from_text(
        f"Example will host a conference call on {call_date} to discuss quarterly results. "
        f"The call will begin at {clock}. "
        f"Example will release its quarterly {release}.{extra}",
        report_day=date(2026, 11, 3),
    )

    assert session == "unknown"


@pytest.mark.parametrize("bound", [
    "the evening before the conference call",
    "one day prior to the conference call",
    "prior to the conference call on the previous day",
])
def test_cross_sentence_prior_day_release_does_not_take_call_date(bound: str) -> None:
    parsed = extract_earnings_announcement(
        "Example will host a conference call on November 3, 2026 to discuss quarterly results. "
        "The call will begin at 8:30 a.m. Eastern Time. "
        f"Example will release its quarterly earnings report {bound}.",
        published_on=date(2026, 10, 23),
    )

    assert parsed == {"date": "2026-11-03", "timing": "unknown"}


@pytest.mark.parametrize("filler", ["", "Registration details are available online. " * 4,
                                    "Registration details are available online. " * 10])
@pytest.mark.parametrize("other_release", [
    "Example will issue fourth quarter results after the market closes on February 2, 2027.",
    "Example will issue fourth quarter results on February 2, 2027 after the market closes.",
    "On February 2, 2027, Example will issue fourth quarter results after the market closes.",
    "Example will issue fourth quarter results on February 2, 2027 at 4:05 p.m. Eastern Time.",
    "Example will issue fourth quarter results at 4:05 p.m. Eastern Time on February 2, 2027.",
    "Example will issue fourth quarter results on February 2, 2027 — after the market closes.",
    "Example will issue fourth quarter results on February 2, 2027 — at 4:05 p.m. Eastern Time.",
    "On February 2, 2027 — Example will issue fourth quarter results after the market closes.",
])
def test_another_event_date_cannot_supply_release_session(
    filler: str, other_release: str,
) -> None:
    parsed = extract_earnings_announcement(
        "Example will report financial results on November 3, 2026. "
        + filler + other_release,
        published_on=date(2026, 10, 23),
    )

    assert parsed == {"date": "2026-11-03", "timing": "unknown"}


@pytest.mark.parametrize("timing", ["after market close", "at 4:05 p.m. Eastern Time"])
def test_historical_release_session_does_not_time_future_event(timing: str) -> None:
    assert _session_from_text(
        "Example will report financial results on November 3, 2026. "
        f"Example reported results on February 2, 2026 {timing}.",
        report_day=date(2026, 11, 3),
    ) == "unknown"
