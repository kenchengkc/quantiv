"""Primary wire search must retain the same issuer and article boundaries."""

from datetime import date
import json

import pytest

import earnings_prnewswire as releases


TODAY = date(2026, 10, 25)
TITLE = "Example Schedules Third Quarter 2026 Earnings Release"
URL = "https://www.prnewswire.com/news-releases/example-schedules-third-quarter-2026-earnings-release-302900276.html"


def article(*, title=TITLE, published="2026-10-07T08:30:00-04:00", body=None, container=True):
    meta = json.dumps({"@type": "NewsArticle", "headline": title, "datePublished": published})
    body = body or "Example (Nasdaq: EXMP) will release earnings results on October 26, 2026, after market close."
    content = f'<section class="release-body container"><p>{body}</p></section>' if container else body
    return f'<script type="application/ld+json">{meta}</script>{content}'


def pages(monkeypatch, *, document=None, links=None):
    calls = []

    def get(url):
        calls.append(url)
        return 200, (links or f'<a href="{URL}">{TITLE}</a>') if "/search/news/" in url else document or article(), None

    monkeypatch.setattr(releases, "_fetch_html", get)
    return calls


def test_primary_release_recovers_session_and_preserves_publication_metadata(monkeypatch):
    calls = pages(monkeypatch)
    rows, meta = releases.fetch_prnewswire_announcements("EXMP", today=TODAY, company_name="Example")
    assert [(r["date"], r["timing"]) for r in rows] == [("2026-10-26", "amc")]
    assert rows[0]["published_on"] == "2026-10-07"
    assert rows[0]["provider"] == "prnewswire_press_release"
    assert rows[0]["url"] == URL
    assert "body" not in rows[0]
    assert len(calls) == meta["requests"] == 2


@pytest.mark.parametrize("document", [
    article(published=""),
    article(published="2026-10-26T08:30:00-04:00"),
    article(published="2025-10-07T08:30:00-04:00"),
    article(title="Other Schedules Third Quarter 2026 Earnings Release"),
    article(body="Other (NYSE: ELSE) will release results on October 26, 2026, after market close."),
    article(body="Example will release results on October 26, 2026, after market close."),
    article(container=False),
])
def test_invalid_article_identity_or_publication_is_not_evidence(monkeypatch, document):
    pages(monkeypatch, document=document)
    rows, _ = releases.fetch_prnewswire_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []


def test_sidebar_cannot_supply_release_session(monkeypatch):
    body = "Example (Nasdaq: EXMP) will release results on October 26, 2026 and hold a call at 5 p.m. Eastern Time."
    document = article(body=body) + '<aside>Example will report financial results after market close on October 26, 2026.</aside>'
    pages(monkeypatch, document=document)
    rows, _ = releases.fetch_prnewswire_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows[0]["timing"] == "unknown"


def test_offsite_links_and_actual_results_are_not_fetched(monkeypatch):
    links = (f'<a href="https://example.invalid/news-releases/example-302900276.html">{TITLE}</a>'
             f'<a href="{URL}">Example Announces Third Quarter 2026 Results</a>')
    calls = pages(monkeypatch, links=links)
    rows, _ = releases.fetch_prnewswire_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []
    assert len(calls) == 1


def test_bounded_discovery_deduplicates_repeated_links(monkeypatch):
    links = "".join(f'<a href="{URL.replace("302900276", str(302900276+n))}">{TITLE}</a>' * 2 for n in range(7))
    calls = pages(monkeypatch, links=links)
    _, meta = releases.fetch_prnewswire_announcements("EXMP", today=TODAY, company_name="Example", max_articles=2)
    assert len(calls) == len(set(calls)) == 3
    assert meta["truncated"] is True


def test_access_block_stops_without_parsing_error_body(monkeypatch):
    monkeypatch.setattr(releases, "_fetch_html", lambda url: (403, None, "HTTP403"))
    rows, meta = releases.fetch_prnewswire_announcements("EXMP", today=TODAY, company_name="Example")
    assert rows == []
    assert (meta["status"], meta["http_status"], meta["requests"]) == ("blocked", 403, 1)
