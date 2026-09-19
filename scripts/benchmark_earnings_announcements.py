#!/usr/bin/env python3
"""Benchmark public announcement/news sources for earnings date confirmation."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests


TRUTH: dict[str, dict[str, str] | None] = {
    "AIR": {"date": "2026-09-29", "session": "amc"},
    "APOG": {"date": "2026-10-06", "session": "bmo"},
    "CAG": {"date": "2026-09-30", "session": "bmo"},
    "FDS": {"date": "2026-09-30", "session": "bmo"},
    "MKC": {"date": "2026-10-01", "session": "bmo"},
    "NKE": {"date": "2026-10-01", "session": "amc"},
    "PRGS": {"date": "2026-09-30", "session": "amc"},
    "ACN": {"date": "2026-10-01", "session": "bmo"},
    "FERG": None,
    "BXMT": None,
    "CCL": {"date": "2026-09-29", "session": "bmo"},
    "JEF": {"date": "2026-09-28", "session": "amc"},
}

MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}
MONTH_PATTERN = "|".join(name.title() for name in MONTHS)
DATE_RE = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\s*(?:ET|EST|EDT|CT|CST|CDT|PT|PST|PDT)?\b",
    re.IGNORECASE,
)
FORWARD_TERMS = (
    "will ",
    "plans to",
    "scheduled",
    "schedule",
    "announce",
    "report",
    "release",
    "publish",
    "conference call",
    "earnings call",
    "financial results",
    "quarter results",
    "quarterly results",
    "results on",
)
EARNINGS_TERMS = (
    "earnings",
    "financial results",
    "quarter results",
    "quarterly results",
    "results",
    "conference call",
)
PAST_TERMS = (
    "reported results",
    "reported earnings",
    "quarter ended",
    "results for the quarter ended",
    "announced results",
)


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _session_from_text(text: str) -> str:
    lower = text.lower()
    bmo_terms = (
        "before market open",
        "before the market opens",
        "before markets open",
        "prior to market open",
        "prior to the market open",
        "pre-market",
        "premarket",
    )
    amc_terms = (
        "after market close",
        "after the market closes",
        "following market close",
        "following the market close",
        "following the close",
        "after the close",
        "post-market",
        "postmarket",
    )
    if any(term in lower for term in bmo_terms):
        return "bmo"
    if any(term in lower for term in amc_terms):
        return "amc"

    times: list[int] = []
    for match in TIME_RE.finditer(text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower().replace(".", "")
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
        times.append(hour * 60 + minute)
    if any(minutes < 9 * 60 + 30 for minutes in times):
        return "bmo"
    if any(minutes >= 16 * 60 for minutes in times):
        return "amc"
    return "unknown"


def _candidate_date(match: re.Match[str], published_on: date) -> date | None:
    month = MONTHS[match.group(1).lower()]
    day = int(match.group(2))
    year_text = match.group(3)
    year = int(year_text) if year_text else published_on.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if not year_text and candidate < published_on:
        try:
            candidate = date(year + 1, month, day)
        except ValueError:
            return None
    return candidate


def extract_earnings_announcement(
    text: str,
    *,
    published_on: date,
) -> dict[str, str] | None:
    cleaned = _clean_text(text)
    lower = cleaned.lower()
    if not any(term in lower for term in EARNINGS_TERMS):
        return None

    candidates: list[tuple[int, date, str]] = []
    for match in DATE_RE.finditer(cleaned):
        candidate = _candidate_date(match, published_on)
        if candidate is None or candidate < published_on:
            continue
        start = max(0, match.start() - 240)
        end = min(len(cleaned), match.end() + 240)
        context = cleaned[start:end]
        context_lower = context.lower()
        score = 0
        if any(term in context_lower for term in EARNINGS_TERMS):
            score += 4
        if any(term in context_lower for term in FORWARD_TERMS):
            score += 4
        if "will" in context_lower or "scheduled" in context_lower:
            score += 2
        if any(term in context_lower for term in PAST_TERMS):
            score -= 6
        distance_days = (candidate - published_on).days
        if 0 <= distance_days <= 120:
            score += 2
        candidates.append((score, candidate, context))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    score, candidate, context = candidates[0]
    if score < 6:
        return None
    session = _session_from_text(context)
    if session == "unknown":
        session = _session_from_text(cleaned)
    return {"date": candidate.isoformat(), "session": session}


def _safe_get(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
    secret: str,
) -> tuple[int, Any | None, str | None]:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=45)
    except requests.RequestException as exc:
        return 0, None, type(exc).__name__
    if not response.ok:
        body = (response.text or "").replace(secret, "[redacted]")
        body = re.sub(r"(?i)(apikey|api_key|token)=[^&\s]+", r"\1=[redacted]", body)
        return response.status_code, None, body[:260]
    try:
        return response.status_code, response.json(), None
    except ValueError:
        return response.status_code, None, "invalid_json"


def _first_env(*names: str) -> str | None:
    for name in names:
        if os.getenv(name):
            return os.getenv(name)
    return None


def _iso_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    compact = re.match(r"(20\d{2})(\d{2})(\d{2})", text)
    if compact:
        try:
            return date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _evidence(
    *,
    title: str,
    body: str,
    published_on: date | None,
    url: str | None,
) -> dict[str, Any] | None:
    if published_on is None:
        return None
    candidate = extract_earnings_announcement(
        f"{title}. {body}",
        published_on=published_on,
    )
    if candidate is None:
        return None
    return {
        **candidate,
        "published_on": published_on.isoformat(),
        "title": _clean_text(title)[:180],
        "url": url,
    }


def fetch_twelvedata(symbol: str) -> dict[str, Any]:
    key = _first_env("TWELVEDATA_API_KEY", "TWELVEDATA_API_KEY_2")
    if not key:
        return {"status": "missing_key", "http_status": None, "evidence": []}
    status, payload, error = _safe_get(
        "https://api.twelvedata.com/press_releases",
        params={
            "symbol": symbol,
            "start_date": "2026-07-01T00:00:00",
            "end_date": "2026-09-19T23:59:59",
            "timezone": "America/New_York",
            "language": "en,en-US",
            "outputsize": 10,
            "page": 1,
            "apikey": key,
        },
        secret=key,
    )
    rows = payload.get("press_releases") if isinstance(payload, dict) else []
    evidence = []
    for row in rows or []:
        item = _evidence(
            title=str(row.get("title") or ""),
            body=str(row.get("body") or ""),
            published_on=_iso_day(row.get("datetime")),
            url=None,
        )
        if item:
            evidence.append(item)
    return {
        "status": "ok" if payload is not None else "error",
        "http_status": status,
        "error": error,
        "article_count": len(rows or []),
        "evidence": evidence,
    }


def fetch_massive(symbol: str) -> dict[str, Any]:
    key = _first_env("POLYGON_API_KEY", "POLYGON_API_KEY_2", "MASSIVE_API_KEY")
    if not key:
        return {"status": "missing_key", "http_status": None, "evidence": []}
    status, payload, error = _safe_get(
        "https://api.massive.com/v2/reference/news",
        params={
            "ticker": symbol,
            "published_utc.gte": "2026-07-01",
            "published_utc.lte": "2026-09-19T23:59:59Z",
            "order": "desc",
            "sort": "published_utc",
            "limit": 50,
        },
        headers={"Authorization": f"Bearer {key}"},
        secret=key,
    )
    rows = payload.get("results") if isinstance(payload, dict) else []
    evidence = []
    for row in rows or []:
        item = _evidence(
            title=str(row.get("title") or ""),
            body=str(row.get("description") or ""),
            published_on=_iso_day(row.get("published_utc")),
            url=row.get("article_url"),
        )
        if item:
            evidence.append(item)
    return {
        "status": "ok" if payload is not None else "error",
        "http_status": status,
        "error": error,
        "article_count": len(rows or []),
        "evidence": evidence,
    }


def fetch_alphavantage(symbol: str) -> dict[str, Any]:
    key = _first_env(
        "ALPHAVANTAGE_API_KEY",
        "ALPHAVANTAGE_API_KEY_2",
        "ALPHAVANTAGE_API_KEY_3",
    )
    if not key:
        return {"status": "missing_key", "http_status": None, "evidence": []}
    status, payload, error = _safe_get(
        "https://www.alphavantage.co/query",
        params={
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "topics": "earnings",
            "time_from": "20260701T0000",
            "time_to": "20260919T2359",
            "sort": "LATEST",
            "limit": 50,
            "apikey": key,
        },
        secret=key,
    )
    if isinstance(payload, dict) and (
        payload.get("Information") or payload.get("Note") or payload.get("Error Message")
    ):
        return {
            "status": "error",
            "http_status": status,
            "error": str(
                payload.get("Information")
                or payload.get("Note")
                or payload.get("Error Message")
            )[:260],
            "article_count": 0,
            "evidence": [],
        }
    rows = payload.get("feed") if isinstance(payload, dict) else []
    evidence = []
    for row in rows or []:
        item = _evidence(
            title=str(row.get("title") or ""),
            body=str(row.get("summary") or ""),
            published_on=_iso_day(row.get("time_published")),
            url=row.get("url"),
        )
        if item:
            evidence.append(item)
    return {
        "status": "ok" if payload is not None else "error",
        "http_status": status,
        "error": error,
        "article_count": len(rows or []),
        "evidence": evidence,
    }


def fetch_finnhub(symbol: str) -> dict[str, Any]:
    key = _first_env("FINNHUB_API_KEY")
    if not key:
        return {"status": "missing_key", "http_status": None, "evidence": []}
    status, payload, error = _safe_get(
        "https://finnhub.io/api/v1/company-news",
        params={
            "symbol": symbol,
            "from": "2026-07-01",
            "to": "2026-09-19",
            "token": key,
        },
        secret=key,
    )
    rows = payload if isinstance(payload, list) else []
    evidence = []
    for row in rows:
        item = _evidence(
            title=str(row.get("headline") or ""),
            body=str(row.get("summary") or ""),
            published_on=_iso_day(row.get("datetime")),
            url=row.get("url"),
        )
        if item:
            evidence.append(item)
    return {
        "status": "ok" if payload is not None else "error",
        "http_status": status,
        "error": error,
        "article_count": len(rows),
        "evidence": evidence,
    }


FETCHERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "twelvedata_press_releases": fetch_twelvedata,
    "massive_stock_news": fetch_massive,
    "alphavantage_news": fetch_alphavantage,
    "finnhub_company_news": fetch_finnhub,
}


def _score(provider: dict[str, dict[str, Any]]) -> dict[str, Any]:
    date_hits = session_hits = 0
    truth_events = sum(expected is not None for expected in TRUTH.values())
    false_positives: list[str] = []
    recovered: dict[str, dict[str, Any]] = {}
    for symbol, expected in TRUTH.items():
        rows = provider.get(symbol, {}).get("evidence", [])
        if expected is None:
            if any("2026-09-21" <= row["date"] <= "2026-10-06" for row in rows):
                false_positives.append(symbol)
            continue
        exact = [row for row in rows if row["date"] == expected["date"]]
        if not exact:
            continue
        date_hits += 1
        best = exact[0]
        if any(row["session"] == expected["session"] for row in exact):
            session_hits += 1
            best = next(row for row in exact if row["session"] == expected["session"])
        recovered[symbol] = best
    return {
        "truth_events": truth_events,
        "date_hits": date_hits,
        "date_accuracy": date_hits / truth_events,
        "session_hits": session_hits,
        "session_accuracy": session_hits / truth_events,
        "false_positives": sorted(false_positives),
        "recovered": recovered,
    }


def run() -> dict[str, Any]:
    providers: dict[str, Any] = {}
    delays = {
        "twelvedata_press_releases": 8.0,
        "alphavantage_news": 1.1,
        "massive_stock_news": 0.15,
        "finnhub_company_news": 0.15,
    }
    for provider_name, fetcher in FETCHERS.items():
        by_symbol: dict[str, Any] = {}
        symbols = list(TRUTH)
        for index, symbol in enumerate(symbols):
            by_symbol[symbol] = fetcher(symbol)
            if index + 1 < len(symbols):
                time.sleep(delays[provider_name])
        providers[provider_name] = {
            "symbols": by_symbol,
            "score": _score(by_symbol),
        }
    return {
        "schema": "quantiv.earnings-announcement-benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": providers,
    }


def _summary(report: dict[str, Any]) -> str:
    lines = [
        "# Earnings announcement benchmark",
        "",
        "| Provider | Date recovered | Session recovered | False positives |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, provider in report["providers"].items():
        score = provider["score"]
        lines.append(
            f"| {name} | {score['date_hits']}/{score['truth_events']} "
            f"({score['date_accuracy']:.1%}) | "
            f"{score['session_hits']}/{score['truth_events']} "
            f"({score['session_accuracy']:.1%}) | "
            f"{', '.join(score['false_positives']) or '-'} |"
        )
    lines.extend(["", "## Recovered evidence", ""])
    for name, provider in report["providers"].items():
        lines.append(f"### {name}")
        for symbol, evidence in sorted(provider["score"]["recovered"].items()):
            lines.append(
                f"- {symbol}: {evidence['date']} {evidence['session']} — "
                f"{evidence['title']}"
            )
        statuses = {
            symbol: {
                "status": result.get("status"),
                "http_status": result.get("http_status"),
                "article_count": result.get("article_count", 0),
                "error": result.get("error"),
            }
            for symbol, result in provider["symbols"].items()
            if result.get("status") != "ok"
        }
        if statuses:
            lines.append(f"- errors: {json.dumps(statuses, sort_keys=True)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/validation/earnings_announcement_benchmark"),
    )
    args = parser.parse_args()
    report = run()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "benchmark.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    summary = _summary(report)
    (args.output_dir / "summary.md").write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
