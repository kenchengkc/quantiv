"""Read bounded, public syndicated issuer releases without retaining raw bodies."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import json
import re
import time
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import requests


BASE_URL = "https://markets.financialcontent.com"
MAX_BYTES = 2_000_000
WIRE_PATH = re.compile(r"^/stocks/article/(?:bizwire|gnwcq)-(20\d{2})-(\d{1,2})-(\d{1,2})-[a-z0-9-]+$")
ISSUER_TICKER = re.compile(
    r"\b(?:NASDAQ|NYSE)(?:\s*(?:GS|GM|CM|American|Global\s+(?:Select\s+)?Market|Capital\s+Market))?"
    r"\s*[:：]\s*([A-Z][A-Z0-9.-]*)", re.I,
)
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _fetch_html(url: str) -> tuple[int | None, str | None, str | None]:
    """No redirects, credentials, retries or raw response persistence."""
    started = time.monotonic()
    try:
        with requests.get(url, timeout=(5, 15), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                return response.status_code, None, f"HTTP{response.status_code}"
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                return response.status_code, None, "unexpected_content_type"
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES or time.monotonic() - started > 20:
                    return response.status_code, None, "response_limit"
                chunks.append(chunk)
            return response.status_code, b"".join(chunks).decode("utf-8", errors="replace"), None
    except requests.RequestException as exc:
        return None, None, type(exc).__name__


class _ReleaseHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor: list[str] = []
        self._depth = 0
        self._body_depth: int | None = None
        self.body: list[str] = []
        self.body_count = 0
        self._ignored = 0
        self._json_script = False
        self._script: list[str] = []
        self.metadata: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag not in VOID_TAGS:
            self._depth += 1
        if tag == "a":
            self._href = attributes.get("href")
            self._anchor = []
        if attributes.get("itemprop") == "articleBody":
            self.body_count += 1
            self._body_depth = self._depth
        if tag in {"script", "style"}:
            self._ignored += 1
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._json_script = True
            self._script = []
        if self._body_depth is not None and tag in {"p", "div", "br", "li"}:
            self.body.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._anchor).split())))
            self._href = None
        if tag == "script" and self._json_script:
            try:
                payload = json.loads("".join(self._script))
                nodes = payload if isinstance(payload, list) else [payload]
                for node in nodes:
                    if isinstance(node, dict):
                        self.metadata.append(node)
                        self.metadata.extend(item for item in node.get("@graph", []) if isinstance(item, dict))
            except (ValueError, TypeError):
                pass
            self._json_script = False
        if tag in {"script", "style"}:
            self._ignored = max(0, self._ignored - 1)
        if tag not in VOID_TAGS and self._body_depth == self._depth:
            self._body_depth = None
        if tag not in VOID_TAGS:
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._json_script:
            self._script.append(data)
        if self._ignored:
            return
        if self._href:
            self._anchor.append(data)
        if self._body_depth is not None:
            self.body.append(data)


def _article_date(url: str) -> date | None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "markets.financialcontent.com"
            or parsed.query or parsed.fragment):
        return None
    match = WIRE_PATH.fullmatch(parsed.path)
    if not match:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _schedules_release(title: str) -> bool:
    """'Announces quarterly results' is an actual release, not a schedule."""
    if re.search(r"\b(?:to|will)\s+(?:announce|report|release|publish|hold|host)\b", title, re.I):
        return True
    if not re.search(r"\bannounces?\b", title, re.I):
        return True
    return bool(re.search(r"\b(?:date|timing|schedule|scheduled|conference call|webcast|earnings release)\b", title, re.I))


def fetch_public_announcements(
    symbol: str, *, today: date, company_name: str | None = None, max_articles: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover issuer releases, then parse only their isolated article bodies.

    Restrict to the two wire URL formats verified against their current public
    listings. Publication metadata must agree with the discovered URL, and the
    article headline must agree with the listing. Other stories and page chrome
    cannot supply release dates or sessions.
    """
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
    listing_url = BASE_URL + "/stocks/quote/news?" + urlencode({"ChannelType": "PRESSRELEASES", "Symbol": symbol})
    status, document, error = _fetch_html(listing_url)
    meta: dict[str, Any] = {"status": "ok", "http_status": status, "requests": 1,
                            "articles": 0, "evidence": 0, "truncated": False}
    if document is None:
        failure_status = "rate_limited" if status == 429 else "blocked" if status == 403 else "error"
        return [], {**meta, "status": failure_status, "error": error}
    listing = _ReleaseHTML()
    listing.feed(document)
    candidates: dict[str, tuple[date, str]] = {}
    for href, title in listing.links:
        url = urljoin(listing_url, href)
        published = _article_date(url)
        if (published is not None and today - timedelta(days=120) <= published <= today
                and is_direct_earnings_announcement_title(title)
                and _schedules_release(title)
                and headline_mentions_company(title, symbol, company_name)):
            candidates[url] = published, title
    ordered = sorted(candidates.items(), key=lambda item: item[1][0], reverse=True)
    meta["truncated"] = len(ordered) > max_articles
    evidence = []
    failures = []
    for url, (expected_day, expected_title) in ordered[:max_articles]:
        status, document, error = _fetch_html(url)
        meta["requests"] += 1
        if document is None:
            failures.append({"url": url, "http_status": status, "error": error})
            if status in {403, 429}:
                meta.update(status="blocked" if status == 403 else "rate_limited", http_status=status)
                break
            continue
        page = _ReleaseHTML()
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
        if published != expected_day or " ".join(title.split()) != expected_title:
            continue
        body = " ".join("".join(page.body).split())
        issuer = ISSUER_TICKER.search(body)
        if (not issuer or issuer.group(1).upper() != symbol
                or not finnhub_article_mentions_company({"headline": title, "summary": body}, symbol, company_name)):
            continue
        meta["articles"] += 1
        item = _announcement_from_article(provider="financialcontent_press_release", title=title,
                                          body=body, published_on=published, official=False, url=url)
        # This collector supplies upcoming events. In particular, a yearless
        # historical date rolled forward by the language parser cannot create
        # a distant future event from an otherwise plausible headline.
        if item and today <= date.fromisoformat(item["date"]) <= today + timedelta(days=120):
            evidence.append(item)
    meta["evidence"] = len(evidence)
    if failures:
        meta.update(failures=failures)
        if meta["status"] == "ok":
            meta["status"] = "partial"
    return evidence, meta
