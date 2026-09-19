"""Regression tests for conservative earnings-calendar reconciliation."""

from __future__ import annotations

from datetime import date

from reconcile_earnings_calendar import (
    choose_canonical_event,
    extract_earnings_announcement,
    is_direct_earnings_announcement_title,
)


def _vote(provider: str, event_date: str, timing: str = "unknown") -> dict[str, str]:
    return {"provider": provider, "date": event_date, "timing": timing}


def _announcement(
    provider: str,
    event_date: str,
    timing: str = "unknown",
    *,
    official: bool = False,
    direct: bool = True,
    published_on: str = "2026-09-18",
) -> dict[str, object]:
    return {
        "provider": provider,
        "date": event_date,
        "timing": timing,
        "official": official,
        "direct": direct,
        "published_on": published_on,
    }


def test_direct_announcement_overrides_conflicting_vendor_dates() -> None:
    current = _vote("dolthub", "2026-09-22")
    baseline = _vote("baseline", "2026-09-21", "amc")
    votes = [
        current,
        _vote("finnhub_calendar", "2026-09-21", "amc"),
        _vote("alphavantage_calendar", "2026-09-22"),
    ]
    announcements = [
        _announcement(
            "finnhub_company_news",
            "2026-09-29",
            "amc",
            direct=True,
        )
    ]

    decision = choose_canonical_event(
        current=current,
        baseline=baseline,
        structured_votes=votes,
        announcements=announcements,
    )

    assert decision["date"] == "2026-09-29"
    assert decision["timing"] == "amc"
    assert decision["reason"] == "direct_announcement"
    assert decision["confidence"] == "confirmed"


def test_single_vendor_revision_cannot_replace_sticky_baseline() -> None:
    current = _vote("dolthub", "2026-10-01", "amc")
    baseline = _vote("baseline", "2026-10-01", "amc")
    votes = [
        current,
        _vote("finnhub_calendar", "2026-09-28", "amc"),
    ]

    decision = choose_canonical_event(
        current=current,
        baseline=baseline,
        structured_votes=votes,
        announcements=[],
    )

    assert decision["date"] == "2026-10-01"
    assert decision["reason"] == "baseline_sticky"


def test_two_independent_structured_sources_can_move_date() -> None:
    current = _vote("dolthub", "2026-09-29")
    baseline = _vote("baseline", "2026-09-29")
    votes = [
        current,
        _vote("finnhub_calendar", "2026-09-30"),
        _vote("alphavantage_calendar", "2026-09-30"),
    ]

    decision = choose_canonical_event(
        current=current,
        baseline=baseline,
        structured_votes=votes,
        announcements=[],
    )

    assert decision["date"] == "2026-09-30"
    assert decision["reason"] == "structured_consensus"
    assert decision["confidence"] == "corroborated"


def test_single_vendor_session_cannot_flip_existing_session() -> None:
    current = _vote("dolthub", "2026-10-01", "bmo")
    baseline = _vote("baseline", "2026-10-01", "amc")
    votes = [
        current,
        _vote("alphavantage_calendar", "2026-10-01", "unknown"),
        _vote("finnhub_calendar", "2026-10-01", "amc"),
    ]

    decision = choose_canonical_event(
        current=current,
        baseline=baseline,
        structured_votes=votes,
        announcements=[],
    )

    assert decision["date"] == "2026-10-01"
    assert decision["timing"] == "bmo"


def test_two_sources_must_agree_before_session_changes_with_new_date() -> None:
    current = _vote("dolthub", "2026-09-29", "bmo")
    baseline = _vote("baseline", "2026-09-29", "bmo")
    votes = [
        current,
        _vote("finnhub_calendar", "2026-09-30", "amc"),
        _vote("alphavantage_calendar", "2026-09-30", "unknown"),
    ]

    decision = choose_canonical_event(
        current=current,
        baseline=baseline,
        structured_votes=votes,
        announcements=[],
    )

    assert decision["date"] == "2026-09-30"
    assert decision["timing"] == "unknown"


def test_official_press_release_wins_over_direct_news_if_they_conflict() -> None:
    decision = choose_canonical_event(
        current=_vote("dolthub", "2026-09-29"),
        baseline=_vote("baseline", "2026-09-29"),
        structured_votes=[],
        announcements=[
            _announcement(
                "finnhub_company_news",
                "2026-09-30",
                direct=True,
                published_on="2026-09-10",
            ),
            _announcement(
                "twelvedata_press_release",
                "2026-10-01",
                "bmo",
                official=True,
                published_on="2026-09-12",
            ),
        ],
    )

    assert decision["date"] == "2026-10-01"
    assert decision["timing"] == "bmo"
    assert decision["reason"] == "official_announcement"


def test_generic_earnings_news_is_not_direct_announcement_evidence() -> None:
    assert is_direct_earnings_announcement_title(
        "AAR to announce first quarter fiscal year 2027 results on September 29, 2026"
    )
    assert is_direct_earnings_announcement_title(
        "NIKE, Inc. Announces First Quarter Fiscal 2027 Earnings and Conference Call"
    )
    assert not is_direct_earnings_announcement_title(
        "Zscaler (ZS) Q4 Earnings and Revenues Top Estimates"
    )
    assert not is_direct_earnings_announcement_title(
        "Nike Suffers a Larger Drop Than the General Market: Key Insights"
    )


def test_extracts_date_and_session_from_forward_announcement() -> None:
    text = (
        "NIKE, Inc. Announces First Quarter Fiscal 2027 Earnings and Conference Call. "
        "NIKE will report results on October 1, 2026 following the close of regular "
        "stock market trading."
    )
    assert extract_earnings_announcement(
        text,
        published_on=date(2026, 9, 10),
    ) == {"date": "2026-10-01", "timing": "amc"}


def test_extractor_rejects_past_results_article() -> None:
    text = "Nike reported fiscal first-quarter results on September 15, 2026."
    assert (
        extract_earnings_announcement(
            text,
            published_on=date(2026, 9, 18),
        )
        is None
    )
