"""Scheduling titles must reach full-body validation without relaxing evidence."""

from datetime import date
import json

import pytest

import earnings_press_releases as releases
import reconcile_earnings_calendar as calendar


TODAY = date(2026, 10, 25)
URL = "https://markets.financialcontent.com/stocks/article/bizwire-2026-10-6-example-earnings-notice"


@pytest.mark.parametrize("title,timing,body", [
    (
        "Example Plans Third Quarter 2026 Earnings Release and Conference Call",
        "bmo",
        "Example (NYSE: EXMP) will release financial results on October 27, 2026 at 7:00 a.m. ET. "
        "A conference call will follow at 8:30 a.m. ET.",
    ),
    (
        "Example to Webcast Third Quarter Fiscal 2026 Earnings Conference Call on October 27, 2026",
        "amc",
        "Example (NYSE: EXMP) will release financial results after market close on October 27, 2026. "
        "The webcast follows at 5:00 p.m. ET.",
    ),
    (
        "Example to Announce 3Q26 Results on October 27, 2026",
        "amc",
        "Example (NYSE: EXMP) will report financial results after market close on October 27, 2026.",
    ),
])
def test_scheduling_title_reaches_verified_full_release(monkeypatch, title, timing, body):
    metadata = json.dumps({"@type": "NewsArticle", "headline": title,
                           "datePublished": "2026-10-06T12:00:00Z"})
    document = (f'<script type="application/ld+json">{metadata}</script>'
                f'<div itemprop="articleBody"><p>{body}</p></div>')

    def fetch(url):
        return 200, f'<a href="{URL}">{title}</a>' if "quote/news?" in url else document, None

    monkeypatch.setattr(releases, "_fetch_html", fetch)
    rows, status = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert [(row["date"], row["timing"]) for row in rows] == [("2026-10-27", timing)]
    assert rows[0]["published_on"] == "2026-10-06"
    assert rows[0]["url"] == URL
    assert status["requests"] == 2


@pytest.mark.parametrize("title", [
    "Example to Webcast Third Quarter 2026 Earnings Conference Call",
    "Example Plans Live Audio Webcast of Third Quarter 2026 Earnings Conference Call",
])
@pytest.mark.parametrize("body", [
    "Example will host an earnings conference call on October 27, 2026 at 7:00 a.m. ET.",
    "Example reported results on July 27, 2026. Its conference call is October 27, 2026 at 5 p.m. ET.",
    "Example will post its earnings call replay on October 27, 2026 at 7 a.m. ET.",
])
def test_webcast_title_without_separate_forward_release_statement_is_rejected(title, body):
    assert calendar._announcement_from_article(provider="test", title=title, body=body,
        published_on=date(2026, 10, 6), official=True, url=URL) is None


@pytest.mark.parametrize("title", [
    "Example Plans Expected Third Quarter 2026 Earnings Release",
    "Analysts Forecast Example to Announce 3Q26 Results",
    "Example Reported 3Q26 Results and Plans an Earnings Release Review",
    "Example Plans Third Quarter 2026 Earnings Preview",
])
def test_estimates_past_results_and_general_plans_are_not_schedules(title):
    assert calendar.is_direct_earnings_announcement_title(title) is False


def test_compact_quarter_actual_results_remain_rejected_by_public_source(monkeypatch):
    title = "Example Announces 3Q26 Results"
    calls = []

    def fetch(url):
        calls.append(url)
        return 200, f'<a href="{URL}">{title}</a>', None

    monkeypatch.setattr(releases, "_fetch_html", fetch)
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []
    assert len(calls) == 1


@pytest.mark.parametrize("mojibake", [False, True])
def test_hawaii_release_passes_title_and_internal_apostrophe_identity(monkeypatch, mojibake):
    title = ("Bank of Hawai‘i Corporation Conference Call to Discuss Third Quarter 2026 "
             "Financial Results and Board Declares Quarterly Preferred Stock Dividends")
    body = ("Bank of Hawai‘i Corporation (NYSE: BOH) will release third quarter 2026 "
            "financial results on October 26, 2026 before the market opens and hold "
            "its conference call at 2:00 p.m. ET.")
    metadata_title = title.encode("utf-8").decode("latin-1") if mojibake else title
    metadata = json.dumps({"@type": "NewsArticle", "headline": metadata_title,
                           "datePublished": "2026-10-06T12:00:00Z"})
    document = (f'<script type="application/ld+json">{metadata}</script>'
                f'<div itemprop="articleBody">{body}</div>')

    def fetch(url):
        return 200, f'<a href="{URL}">{title}</a>' if "quote/news?" in url else document, None

    monkeypatch.setattr(releases, "_fetch_html", fetch)
    rows, _ = releases.fetch_public_announcements("BOH", today=TODAY, company_name="Bank of Hawaii")
    assert [(row["date"], row["timing"]) for row in rows] == [("2026-10-26", "bmo")]
    wrong_company, _ = releases.fetch_public_announcements("BAC", today=TODAY, company_name="Bank of America")
    assert wrong_company == []


def test_discussion_title_does_not_turn_a_call_clock_into_a_release():
    assert calendar._announcement_from_article(
        provider="test", title="Example Conference Call to Discuss Third Quarter 2026 Financial Results",
        body="Example will hold a conference call on October 27, 2026 at 7 a.m. ET.",
        published_on=date(2026, 10, 6), official=True, url=URL,
    ) is None


def test_discussion_of_another_subject_is_not_an_earnings_schedule():
    assert calendar.is_direct_earnings_announcement_title(
        "Example Conference Call to Discuss Planned Acquisitions"
    ) is False


@pytest.mark.parametrize("title", [
    "Example Plans to Discuss Third Quarter 2026 Earnings Release",
    "Example Plans Third Quarter 2026 Earnings Release Review",
])
def test_planned_release_discussion_cannot_move_calendar_from_call_only_body(monkeypatch, title):
    body = "Example (NYSE: EXMP) will hold a conference call on October 27, 2026 at 7 a.m. ET."
    metadata = json.dumps({"@type": "NewsArticle", "headline": title,
                           "datePublished": "2026-10-06T12:00:00Z"})
    document = (f'<script type="application/ld+json">{metadata}</script>'
                f'<div itemprop="articleBody">{body}</div>')

    def fetch(url):
        return 200, f'<a href="{URL}">{title}</a>' if "quote/news?" in url else document, None

    monkeypatch.setattr(releases, "_fetch_html", fetch)
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []


def test_planned_passive_release_statement_is_separate_from_call():
    result = calendar._announcement_from_article(
        provider="test", title="Example Plans Third Quarter 2026 Earnings Release and Conference Call",
        body=("Example announced that its financial results are scheduled to be released "
              "at 7:00 a.m. ET on October 27, 2026. The call follows at 8:30 a.m. ET."),
        published_on=date(2026, 10, 6), official=True, url=URL,
    )
    assert result is not None
    assert (result["date"], result["timing"]) == ("2026-10-27", "bmo")


def test_mojibake_repair_does_not_relax_listing_headline_equality(monkeypatch):
    title = "Bank of Hawai‘i Plans Third Quarter 2026 Earnings Release"
    other_title = title.replace("Third", "Fourth").encode("utf-8").decode("latin-1")
    metadata = json.dumps({"@type": "NewsArticle", "headline": other_title,
                           "datePublished": "2026-10-06T12:00:00Z"})
    document = (f'<script type="application/ld+json">{metadata}</script>'
                '<div itemprop="articleBody">Bank of Hawai‘i (NYSE: BOH) will release '
                'financial results before market opens on October 26, 2026.</div>')

    def fetch(url):
        return 200, f'<a href="{URL}">{title}</a>' if "quote/news?" in url else document, None

    monkeypatch.setattr(releases, "_fetch_html", fetch)
    rows, _ = releases.fetch_public_announcements("BOH", today=TODAY, company_name="Bank of Hawaii")
    assert rows == []
