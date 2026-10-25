"""Full release retrieval must preserve issuer, article and publication identity."""

from datetime import date
import json

import earnings_press_releases as releases


TODAY = date(2026, 10, 25)
URL = "https://markets.financialcontent.com/stocks/article/bizwire-2026-10-12-example-schedules-earnings"
TITLE = "Example Schedules Third Quarter 2026 Earnings Conference Call"


def article(*, title=TITLE, published="2026-10-12T11:00:00Z", body=None):
    metadata = json.dumps({"@type": "NewsArticle", "headline": title, "datePublished": published})
    body = body or ("Example (NASDAQ: EXMP) will report financial results before the market opens "
                    "on October 26, 2026.")
    return (f'<script type="application/ld+json">{metadata}</script>'
            f'<div itemprop="articleBody"><article><p>{body}</p></article></div>'
            '<aside>Other Company will report results after market close November 5, 2026.</aside>')


def mock_pages(monkeypatch, article_html=None, links=None):
    calls = []
    listing = links or f'<a href="{URL}">{TITLE}</a>'

    def get(url):
        calls.append(url)
        return (200, listing if "quote/news?" in url else article_html or article(), None)

    monkeypatch.setattr(releases, "_fetch_html", get)
    return calls


def test_full_body_recovers_session_without_sidebar_contamination(monkeypatch):
    calls = mock_pages(monkeypatch)
    rows, meta = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert [(r["date"], r["timing"]) for r in rows] == [("2026-10-26", "bmo")]
    assert rows[0]["url"] == URL
    assert rows[0]["published_on"] == "2026-10-12"
    assert rows[0]["provider"] == "financialcontent_press_release"
    assert "body" not in rows[0]
    assert len(calls) == meta["requests"] == 2


def test_wrong_issuer_body_and_changed_article_title_fail_closed(monkeypatch):
    for html in [article(body="Other (NASDAQ: ELSE) will report results before market opens on October 26, 2026."),
                 article(title="Other Schedules Third Quarter 2026 Earnings Conference Call")]:
        mock_pages(monkeypatch, html)
        rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
        assert rows == []


def test_missing_future_or_mismatched_publication_date_is_not_evidence(monkeypatch):
    for published in ["", "2026-10-26T11:00:00Z", "2026-10-13T11:00:00Z"]:
        mock_pages(monkeypatch, article(published=published))
        rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
        assert rows == []


def test_untrusted_links_and_old_articles_never_get_fetched(monkeypatch):
    links = (f'<a href="https://example.invalid/article/bizwire-2026-10-12-earnings">{TITLE}</a>'
             f'<a href="https://markets.financialcontent.com/stocks/article/bizwire-2025-10-12-earnings">{TITLE}</a>'
             f'<a href="https://markets.financialcontent.com/stocks/article/analyst-2026-10-12-earnings">{TITLE}</a>')
    calls = mock_pages(monkeypatch, links=links)
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []
    assert len(calls) == 1


def test_duplicate_links_and_request_cap(monkeypatch):
    links = "".join(f'<a href="{URL}-{n}">{TITLE}</a>' * 2 for n in range(8))
    calls = mock_pages(monkeypatch, links=links)
    _, meta = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example", max_articles=2)
    assert len(calls) == 3
    assert len(set(calls)) == 3
    assert meta["truncated"] is True


def test_failed_fetch_never_parses_an_error_page(monkeypatch):
    monkeypatch.setattr(releases, "_fetch_html", lambda url: (429, None, "HTTP429"))
    rows, meta = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []
    assert meta["status"] == "rate_limited"
    assert meta["requests"] == 1


def test_call_only_article_keeps_unknown_session(monkeypatch):
    mock_pages(monkeypatch, article(body="Example (NASDAQ: EXMP) will report results on October 26, 2026 and hold a conference call at 5 p.m. Eastern Time."))
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows[0]["timing"] == "unknown"


def test_actual_results_title_cannot_create_a_future_event_from_old_body_dates(monkeypatch):
    title = "Example Announces Third Quarter 2026 Results"
    body = ("Example (NASDAQ: EXMP) announces its financial results today. "
            "A dividend was announced on July 27 and will be paid at 7 a.m. Eastern Time.")
    calls = mock_pages(monkeypatch, article(title=title, body=body), links=f'<a href="{URL}">{title}</a>')
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []
    assert len(calls) == 1


def test_announcement_of_results_schedule_remains_eligible(monkeypatch):
    title = "Example Announces Third Quarter 2026 Earnings Release and Conference Call"
    calls = mock_pages(monkeypatch, article(title=title), links=f'<a href="{URL}">{title}</a>')
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert [(r["date"], r["timing"]) for r in rows] == [("2026-10-26", "bmo")]
    assert len(calls) == 2


def test_implausible_year_rollover_is_excluded_even_with_scheduling_title(monkeypatch):
    mock_pages(monkeypatch, article(body="Example (NASDAQ: EXMP) will report its earnings results before market opens on July 27."))
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []


def test_contemporaneous_conflicting_releases_are_both_retained(monkeypatch):
    listing = f'<a href="{URL}-one">{TITLE}</a><a href="{URL}-two">{TITLE}</a>'

    def get(url):
        if "quote/news?" in url:
            return 200, listing, None
        body = ("Example (NASDAQ: EXMP) will report financial results on October 26, 2026 "
                + ("before market opens." if url.endswith("one") else "after market close."))
        return 200, article(body=body), None

    monkeypatch.setattr(releases, "_fetch_html", get)
    rows, _ = releases.fetch_public_announcements("EXMP", today=TODAY, company_name="Example")
    assert {r["timing"] for r in rows} == {"bmo", "amc"}


def test_shared_company_name_without_explicit_issuer_ticker_is_not_confirmation(monkeypatch):
    title = "Granite Point Schedules Third Quarter 2026 Earnings Conference Call"
    body = "Granite Point will report financial results before market opens on October 26, 2026."
    mock_pages(monkeypatch, article(title=title, body=body), links=f'<a href="{URL}">{title}</a>')
    rows, _ = releases.fetch_public_announcements("GVA", today=TODAY, company_name="Granite Construction")
    assert rows == []
