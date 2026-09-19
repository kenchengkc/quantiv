"""Regression tests for conservative earnings-calendar reconciliation."""

from __future__ import annotations

from datetime import date

import pandas as pd

from reconcile_earnings_calendar import (
    alpha_article_mentions_symbol,
    choose_canonical_event,
    extract_earnings_announcement,
    extract_fiscal_identity,
    is_direct_earnings_announcement_title,
    reconcile_calendar,
    _candidate_symbols,
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
    assert not is_direct_earnings_announcement_title(
        "Company Announces Results of Tender Offer on September 29"
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



def test_prior_quarter_announcement_cannot_override_current_event() -> None:
    decision = choose_canonical_event(
        current=_vote("dolthub", "2026-09-24", "bmo"),
        baseline=_vote("baseline", "2026-09-24", "bmo"),
        structured_votes=[],
        announcements=[
            _announcement(
                "finnhub_company_news",
                "2026-07-30",
                "bmo",
                direct=True,
                published_on="2026-07-14",
            )
        ],
        as_of=date(2026, 9, 19),
    )

    assert decision["date"] == "2026-09-24"
    assert decision["reason"] == "baseline_sticky"


def test_alpha_news_requires_target_ticker_relevance() -> None:
    assert alpha_article_mentions_symbol(
        {
            "ticker_sentiment": [
                {"ticker": "JEF", "relevance_score": "0.91"},
                {"ticker": "SPY", "relevance_score": "0.10"},
            ]
        },
        "JEF",
    )
    assert not alpha_article_mentions_symbol(
        {
            "ticker_sentiment": [
                {"ticker": "ZS", "relevance_score": "0.98"},
                {"ticker": "ACN", "relevance_score": "0.02"},
            ]
        },
        "ACN",
        min_relevance=0.1,
    )



def test_announcement_candidates_are_frontend_bounded_and_nearest_first() -> None:
    current = pd.DataFrame(
        [
            {"act_symbol": "AAA", "date": date(2026, 9, 20)},
            {"act_symbol": "BBB", "date": date(2026, 9, 21)},
            {"act_symbol": "CCC", "date": date(2026, 9, 25)},
        ]
    )
    baseline = pd.DataFrame(columns=["act_symbol", "date"])
    provider_votes = [
        {"provider": "finnhub_calendar", "symbol": "DDD", "date": "2026-09-19", "timing": "unknown"}
    ]

    symbols = _candidate_symbols(
        current,
        baseline,
        provider_votes,
        start=date(2026, 9, 19),
        end=date(2026, 10, 10),
        allowed_symbols={"AAA", "CCC", "DDD"},
        max_symbols=2,
    )

    assert symbols == ["DDD", "AAA"]



def test_confirmed_baseline_survives_later_structured_vendor_consensus() -> None:
    baseline = {
        "provider": "baseline",
        "date": "2026-10-01",
        "timing": "amc",
        "confidence": "confirmed",
    }
    decision = choose_canonical_event(
        current=_vote("dolthub", "2026-10-01", "amc"),
        baseline=baseline,
        structured_votes=[
            _vote("finnhub_calendar", "2026-09-28", "amc"),
            _vote("alphavantage_calendar", "2026-09-28"),
        ],
        announcements=[],
        as_of=date(2026, 9, 19),
    )

    assert decision["date"] == "2026-10-01"
    assert decision["timing"] == "amc"
    assert decision["reason"] == "baseline_confirmed"
    assert decision["confidence"] == "confirmed"


def test_reconcile_does_not_create_event_outside_allowed_frontend_universe() -> None:
    current = pd.DataFrame(
        [
            {
                "act_symbol": "AAA",
                "date": date(2026, 9, 25),
                "timing": "unknown",
                "fiscal_year": 2026,
                "fiscal_q": "Q3",
                "eps_actual": None,
                "eps_estimate": None,
                "revenue_actual": None,
                "revenue_estimate": None,
                "source": "dolthub",
            }
        ]
    )
    baseline = current.copy()
    votes = [
        {
            "provider": "finnhub_calendar",
            "symbol": "ZZZ",
            "date": "2026-09-30",
            "timing": "amc",
        },
        {
            "provider": "alphavantage_calendar",
            "symbol": "ZZZ",
            "date": "2026-09-30",
            "timing": "unknown",
        },
    ]

    reconciled, report = reconcile_calendar(
        current,
        baseline,
        start=date(2026, 9, 19),
        end=date(2026, 11, 18),
        structured_votes=votes,
        announcements_by_symbol={},
        allowed_symbols={"AAA"},
    )

    assert "ZZZ" not in set(reconciled["act_symbol"])
    assert "ZZZ" not in report["decisions"]



def test_large_direct_announcement_move_replaces_old_anchor_without_duplicate() -> None:
    current = pd.DataFrame(
        [
            {
                "act_symbol": "FERG",
                "date": date(2026, 9, 22),
                "timing": "unknown",
                "fiscal_year": 2026,
                "fiscal_q": "Q3",
                "eps_actual": None,
                "eps_estimate": 2.0,
                "revenue_actual": None,
                "revenue_estimate": None,
                "source": "dolthub",
            }
        ]
    )
    baseline = current.copy()
    announcements = {
        "FERG": [
            _announcement(
                "finnhub_company_news",
                "2026-11-09",
                "bmo",
                direct=True,
                published_on="2026-09-18",
            )
        ]
    }

    reconciled, _ = reconcile_calendar(
        current,
        baseline,
        start=date(2026, 9, 19),
        end=date(2026, 11, 18),
        structured_votes=[],
        announcements_by_symbol=announcements,
        allowed_symbols={"FERG"},
    )

    rows = reconciled[reconciled["act_symbol"].eq("FERG")]
    assert rows["date"].tolist() == [date(2026, 11, 9)]
    assert rows["timing"].tolist() == ["bmo"]



def test_extracts_fiscal_identity_from_direct_announcement_title() -> None:
    assert extract_fiscal_identity(
        "McCormick & Company to Report 2026 Third Quarter Financial Results on October 1, 2026"
    ) == {"fiscal_year": 2026, "fiscal_q": "Q3"}
    assert extract_fiscal_identity(
        "AAR to announce first quarter fiscal year 2027 results on September 29, 2026"
    ) == {"fiscal_year": 2027, "fiscal_q": "Q1"}


def test_new_announcement_event_uses_announced_fiscal_identity_not_report_month() -> None:
    current = pd.DataFrame(columns=[
        "act_symbol", "date", "timing", "fiscal_year", "fiscal_q",
        "eps_actual", "eps_estimate", "revenue_actual", "revenue_estimate", "source",
    ])
    baseline = current.copy()
    announcements = {
        "MKC": [
            {
                "provider": "finnhub_company_news",
                "date": "2026-10-01",
                "timing": "bmo",
                "official": False,
                "direct": True,
                "published_on": "2026-09-16",
                "fiscal_year": 2026,
                "fiscal_q": "Q3",
            }
        ]
    }

    reconciled, _ = reconcile_calendar(
        current,
        baseline,
        start=date(2026, 9, 19),
        end=date(2026, 11, 18),
        structured_votes=[],
        announcements_by_symbol=announcements,
        allowed_symbols={"MKC"},
    )

    row = reconciled[reconciled["act_symbol"].eq("MKC")].iloc[0]
    assert row["date"] == date(2026, 10, 1)
    assert row["fiscal_year"] == 2026
    assert row["fiscal_q"] == "Q3"
