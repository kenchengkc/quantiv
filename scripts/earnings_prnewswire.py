"""Bounded primary-wire discovery for upcoming issuer earnings announcements."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from earnings_press_releases import ISSUER_TICKER, _ReleaseHTML, _fetch_html, _schedules_release


BASE_URL = "https://www.prnewswire.com"
ARTICLE_PATH = re.compile(r"^/news-releases/[a-z0-9-]+-\d+\.html$")


class _WireHTML(_ReleaseHTML):
    """Capture the publisher's release section, excluding all surrounding news."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        attributes.pop("itemprop", None)
        if tag == "section" and "release-body" in (attributes.get("class") or "").split():
            attributes["itemprop"] = "articleBody"
        super().handle_starttag(tag, list(attributes.items()))


def _article_url(href: str, listing_url: str) -> str | None:
    url = urljoin(listing_url, href)
    parsed = urlsplit(url)
    if (parsed.scheme == "https" and parsed.netloc == "www.prnewswire.com"
            and not parsed.query and not parsed.fragment and ARTICLE_PATH.fullmatch(parsed.path)):
        return url
    return None


def fetch_prnewswire_announcements(
    symbol: str, *, today: date, company_name: str | None = None, max_articles: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from reconcile_earnings_calendar import (
        _announcement_from_article,
        finnhub_article_mentions_company,
        headline_mentions_company,
        is_direct_earnings_announcement_title,
    )

    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
        raise ValueError("invalid ticker")
    if not 1 <= max_articles <= 4:
        raise ValueError("max_articles must be between 1 and 4")
    listing_url = BASE_URL + "/search/news/?" + urlencode({"keyword": symbol})
    status, document, error = _fetch_html(listing_url)
    meta: dict[str, Any] = {"status": "ok", "http_status": status, "requests": 1,
                            "articles": 0, "evidence": 0, "truncated": False}
    if document is None:
        failure_status = "rate_limited" if status == 429 else "blocked" if status == 403 else "error"
        return [], {**meta, "status": failure_status, "error": error}
    listing = _WireHTML()
    listing.feed(document)
    candidates: dict[str, str] = {}
    for href, title in listing.links:
        url = _article_url(href, listing_url)
        if (url and is_direct_earnings_announcement_title(title) and _schedules_release(title)
                and headline_mentions_company(title, symbol, company_name)):
            candidates[url] = title
    meta["truncated"] = len(candidates) > max_articles
    evidence = []
    failures = []
    for url, expected_title in list(candidates.items())[:max_articles]:
        status, document, error = _fetch_html(url)
        meta["requests"] += 1
        if document is None:
            failures.append({"url": url, "http_status": status, "error": error})
            if status in {403, 429}:
                meta.update(status="blocked" if status == 403 else "rate_limited", http_status=status)
                break
            continue
        page = _WireHTML()
        page.feed(document)
        articles = [item for item in page.metadata if item.get("@type") == "NewsArticle"]
        if len(articles) != 1 or page.body_count != 1:
            continue
        metadata = articles[0]
        title = str(metadata.get("headline") or "")
        try:
            published = datetime.fromisoformat(str(metadata.get("datePublished") or "").replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if not today - timedelta(days=120) <= published <= today or " ".join(title.split()) != expected_title:
            continue
        body = " ".join("".join(page.body).split())
        issuer = ISSUER_TICKER.search(body)
        if (not issuer or issuer.group(1).upper() != symbol
                or not finnhub_article_mentions_company({"headline": title, "summary": body}, symbol, company_name)):
            continue
        meta["articles"] += 1
        item = _announcement_from_article(provider="prnewswire_press_release", title=title,
                                          body=body, published_on=published, official=True, url=url)
        if item and today <= date.fromisoformat(item["date"]) <= today + timedelta(days=120):
            evidence.append(item)
    meta["evidence"] = len(evidence)
    if failures:
        meta.update(failures=failures)
        if meta["status"] == "ok":
            meta["status"] = "partial"
    return evidence, meta
