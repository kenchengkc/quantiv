#!/usr/bin/env python3
"""Conservatively reconcile near-term earnings dates across independent sources.

Production contract:
- DoltHub supplies the base calendar/history.
- Prior published calendar is sticky when providers disagree.
- A single structured vendor cannot move an existing event.
- Two independent structured sources may corroborate a new date.
- Official/direct forward-looking company announcements may override estimates.
- Session changes are stricter than date changes.
- Raw announcement bodies are never persisted.

The script writes the reconciled earnings_calendar.{csv,parquet} plus a
sanitized decision report under data/validation.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from sync_finnhub_earnings import (
    OUTPUT_COLUMNS,
    default_data_dir,
    et_today,
    fiscal_q_from_date,
    load_frontend_symbol_universe,
    normalize_existing,
    write_outputs,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "data" / "validation" / "earnings_calendar_baseline.csv"
DEFAULT_REPORT = REPO_ROOT / "data" / "validation" / "earnings_calendar_reconciliation.json"

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
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\s*"
    r"(?:ET|EST|EDT|CT|CST|CDT|PT|PST|PDT)?\b",
    re.IGNORECASE,
)
EARNINGS_TERMS = (
    "earnings",
    "financial results",
    "quarter results",
    "quarterly results",
    "results",
    "conference call",
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
    "to hold",
    "conference call",
    "earnings call",
    "results on",
)
PAST_TERMS = (
    "reported results",
    "reported earnings",
    "quarter ended",
    "results for the quarter ended",
    "announced results",
    "top estimates",
    "beats estimates",
    "misses estimates",
)
DIRECT_TITLE_ACTIONS = (
    " to announce ",
    " to report ",
    " to release ",
    " to hold ",
    " announces ",
    " schedules ",
    " scheduled ",
    " will announce ",
    " will report ",
    " will release ",
)
DIRECT_TITLE_RESULTS = (
    "earnings",
    "financial results",
    "quarter results",
    "quarterly results",
    "conference call",
)

QUARTER_WORDS = {
    "first": "Q1",
    "second": "Q2",
    "third": "Q3",
    "fourth": "Q4",
}
DIRECT_TITLE_PAST = (
    "top estimates",
    "beats estimates",
    "misses estimates",
    "reported ",
    "reports better",
    "revenues top",
)

Vote = dict[str, Any]
Announcement = dict[str, Any]


def normalize_timing(value: Any) -> str:
    s = re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())
    if s in {
        "bmo",
        "before market open",
        "before open",
        "pre market",
        "premarket",
        "pre-market",
    }:
        return "bmo"
    if s in {
        "amc",
        "after market close",
        "after close",
        "after hours",
        "post market",
        "postmarket",
        "post-market",
    }:
        return "amc"
    if s in {"dmh", "during market hours", "during market hour"}:
        return "dmh"
    return "unknown"


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_direct_earnings_announcement_title(title: str) -> bool:
    text = f" {_clean_text(title).lower()} "
    if any(term in text for term in DIRECT_TITLE_PAST):
        return False
    if not any(term in text for term in DIRECT_TITLE_ACTIONS):
        return False
    if any(term in text for term in DIRECT_TITLE_RESULTS):
        return True
    quarter_context = re.search(
        r"\\b(?:first|second|third|fourth)\\s+quarter\\b|\\bq[1-4]\\b",
        text,
        flags=re.IGNORECASE,
    )
    return bool(quarter_context and " results " in text)


def extract_fiscal_identity(text: str) -> dict[str, Any]:
    cleaned = _clean_text(text)

    patterns: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(
                r"\\b(20\\d{2})\\s+(first|second|third|fourth)\\s+quarter\\b",
                re.IGNORECASE,
            ),
            "year_first",
        ),
        (
            re.compile(
                r"\\b(first|second|third|fourth)\\s+quarter"
                r"(?:\\s+(?:of\\s+)?)?(?:fiscal(?:\\s+year)?\\s*)?(20\\d{2})\\b",
                re.IGNORECASE,
            ),
            "quarter_first",
        ),
        (
            re.compile(r"\\bfy\\s*(20\\d{2})\\s*q([1-4])\\b", re.IGNORECASE),
            "fy_q",
        ),
        (
            re.compile(r"\\bq([1-4])\\s*(?:fy\\s*)?(20\\d{2})\\b", re.IGNORECASE),
            "q_fy",
        ),
    ]

    for pattern, mode in patterns:
        match = pattern.search(cleaned)
        if not match:
            continue
        if mode == "year_first":
            fiscal_year = int(match.group(1))
            fiscal_q = QUARTER_WORDS[match.group(2).lower()]
        elif mode == "quarter_first":
            fiscal_q = QUARTER_WORDS[match.group(1).lower()]
            fiscal_year = int(match.group(2))
        elif mode == "fy_q":
            fiscal_year = int(match.group(1))
            fiscal_q = f"Q{match.group(2)}"
        else:
            fiscal_q = f"Q{match.group(1)}"
            fiscal_year = int(match.group(2))
        return {"fiscal_year": fiscal_year, "fiscal_q": fiscal_q}

    quarter_match = re.search(
        r"\\b(first|second|third|fourth)\\s+quarter\\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if quarter_match:
        nearby = cleaned[
            quarter_match.start() : min(len(cleaned), quarter_match.end() + 60)
        ]
        year_match = re.search(
            r"\\bfiscal(?:\\s+year)?\\s+(20\\d{2})\\b",
            nearby,
            flags=re.IGNORECASE,
        )
        if year_match:
            return {
                "fiscal_year": int(year_match.group(1)),
                "fiscal_q": QUARTER_WORDS[quarter_match.group(1).lower()],
            }

    return {}


def _session_from_text(text: str) -> str:
    lower = text.lower()
    if any(
        term in lower
        for term in (
            "before market open",
            "before the market opens",
            "before markets open",
            "prior to market open",
            "prior to the market open",
            "pre-market",
            "premarket",
        )
    ):
        return "bmo"
    if any(
        term in lower
        for term in (
            "after market close",
            "after the market closes",
            "following market close",
            "following the market close",
            "following the close",
            "after the close",
            "post-market",
            "postmarket",
        )
    ):
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
        start = max(0, match.start() - 260)
        end = min(len(cleaned), match.end() + 260)
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
        if 0 <= (candidate - published_on).days <= 180:
            score += 2
        candidates.append((score, candidate, context))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    score, candidate, context = candidates[0]
    if score < 6:
        return None

    timing = _session_from_text(context)
    if timing == "unknown":
        timing = _session_from_text(cleaned)
    return {"date": candidate.isoformat(), "timing": timing}


def _distinct_votes(votes: list[Vote]) -> list[Vote]:
    by_provider: dict[str, Vote] = {}
    for vote in votes:
        provider = str(vote.get("provider") or "").strip()
        event_date = str(vote.get("date") or "").strip()[:10]
        if not provider or not event_date:
            continue
        by_provider[provider] = {
            **vote,
            "provider": provider,
            "date": event_date,
            "timing": normalize_timing(vote.get("timing")),
        }
    return list(by_provider.values())


def _latest_announcement(rows: list[Announcement]) -> Announcement:
    return max(
        rows,
        key=lambda row: (
            str(row.get("published_on") or ""),
            str(row.get("provider") or ""),
        ),
    )


def alpha_article_mentions_symbol(
    article: dict[str, Any],
    symbol: str,
    *,
    min_relevance: float = 0.1,
) -> bool:
    """Require Alpha Vantage news to be materially relevant to the target ticker."""
    target = symbol.strip().upper()
    for item in article.get("ticker_sentiment") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("ticker") or "").strip().upper() != target:
            continue
        try:
            relevance = float(item.get("relevance_score"))
        except (TypeError, ValueError):
            continue
        if relevance >= min_relevance:
            return True
    return False


def _announcement_decision(
    announcements: list[Announcement],
    *,
    as_of: date | None = None,
) -> tuple[Announcement | None, str | None]:
    valid = [
        {
            **row,
            "timing": normalize_timing(row.get("timing")),
        }
        for row in announcements
        if row.get("date")
        and (row.get("official") or row.get("direct"))
        and (
            as_of is None
            or date.fromisoformat(str(row.get("date"))[:10]) >= as_of
        )
    ]
    official = [row for row in valid if row.get("official")]
    if official:
        return _latest_announcement(official), "official_announcement"
    direct = [row for row in valid if row.get("direct")]
    if direct:
        counts = Counter(str(row["date"]) for row in direct)
        max_count = max(counts.values())
        top_dates = {d for d, count in counts.items() if count == max_count}
        top_rows = [row for row in direct if str(row["date"]) in top_dates]
        return _latest_announcement(top_rows), "direct_announcement"
    return None, None


def _structured_date_consensus(votes: list[Vote]) -> tuple[str | None, list[str]]:
    distinct = _distinct_votes(votes)
    by_date: dict[str, list[str]] = defaultdict(list)
    for vote in distinct:
        by_date[vote["date"]].append(vote["provider"])
    if not by_date:
        return None, []
    ranked = sorted(
        by_date.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    best_date, providers = ranked[0]
    if len(providers) < 2:
        return None, []
    if len(ranked) > 1 and len(ranked[1][1]) == len(providers):
        return None, []
    return best_date, sorted(providers)


def _session_consensus(votes: list[Vote], event_date: str) -> tuple[str | None, list[str]]:
    distinct = _distinct_votes(votes)
    by_timing: dict[str, list[str]] = defaultdict(list)
    for vote in distinct:
        if vote["date"] != event_date:
            continue
        timing = normalize_timing(vote.get("timing"))
        if timing in {"bmo", "amc", "dmh"}:
            by_timing[timing].append(vote["provider"])
    if not by_timing:
        return None, []
    ranked = sorted(
        by_timing.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    timing, providers = ranked[0]
    if len(providers) < 2:
        return None, []
    if len(ranked) > 1 and len(ranked[1][1]) == len(providers):
        return None, []
    return timing, sorted(providers)


def choose_canonical_event(
    *,
    current: Vote | None,
    baseline: Vote | None,
    structured_votes: list[Vote],
    announcements: list[Announcement],
    as_of: date | None = None,
) -> dict[str, Any]:
    current = (
        {
            **current,
            "date": str(current.get("date") or "")[:10],
            "timing": normalize_timing(current.get("timing")),
        }
        if current
        else None
    )
    baseline = (
        {
            **baseline,
            "date": str(baseline.get("date") or "")[:10],
            "timing": normalize_timing(baseline.get("timing")),
        }
        if baseline
        else None
    )
    votes = _distinct_votes(structured_votes)

    announcement, announcement_reason = _announcement_decision(
        announcements,
        as_of=as_of,
    )
    baseline_confirmed = bool(
        baseline
        and baseline.get("date")
        and str(baseline.get("confidence") or "").lower()
        in {"confirmed", "manual"}
    )
    if announcement is not None:
        chosen_date = str(announcement["date"])[:10]
        reason = announcement_reason
        confidence = "confirmed"
        date_sources = [str(announcement.get("provider") or "announcement")]
    elif baseline_confirmed:
        chosen_date = baseline["date"]
        reason = "baseline_confirmed"
        confidence = "confirmed"
        date_sources = ["baseline"]
    else:
        consensus_date, consensus_sources = _structured_date_consensus(votes)
        if consensus_date:
            chosen_date = consensus_date
            reason = "structured_consensus"
            confidence = "corroborated"
            date_sources = consensus_sources
        elif baseline and baseline.get("date"):
            chosen_date = baseline["date"]
            reason = "baseline_sticky"
            confidence = "sticky"
            date_sources = ["baseline"]
        elif current and current.get("date"):
            chosen_date = current["date"]
            reason = "current_base"
            confidence = "projected"
            date_sources = [str(current.get("provider") or "current")]
        elif votes:
            # Do not create a new event from one uncorroborated external vendor.
            return {
                "date": None,
                "timing": "unknown",
                "reason": "uncorroborated_new_event",
                "confidence": "unresolved",
                "date_sources": [],
                "timing_sources": [],
            }
        else:
            return {
                "date": None,
                "timing": "unknown",
                "reason": "no_candidate",
                "confidence": "unresolved",
                "date_sources": [],
                "timing_sources": [],
            }

    timing = "unknown"
    timing_sources: list[str] = []

    if announcement is not None and str(announcement["date"])[:10] == chosen_date:
        announcement_timing = normalize_timing(announcement.get("timing"))
        if announcement_timing != "unknown":
            timing = announcement_timing
            timing_sources = [str(announcement.get("provider") or "announcement")]

    if timing == "unknown":
        consensus_timing, session_sources = _session_consensus(votes, chosen_date)
        if consensus_timing:
            timing = consensus_timing
            timing_sources = session_sources

    if (
        timing == "unknown"
        and baseline_confirmed
        and baseline
        and baseline.get("date") == chosen_date
        and baseline.get("timing") != "unknown"
    ):
        timing = str(baseline["timing"])
        timing_sources = ["baseline"]

    # A single vendor cannot flip an established session. For unconfirmed
    # events prefer the fresh base row over a merely sticky baseline.
    if (
        timing == "unknown"
        and current
        and current.get("date") == chosen_date
        and current.get("timing") != "unknown"
    ):
        timing = str(current["timing"])
        timing_sources = [str(current.get("provider") or "current")]
    if (
        timing == "unknown"
        and baseline
        and baseline.get("date") == chosen_date
        and baseline.get("timing") != "unknown"
    ):
        timing = str(baseline["timing"])
        timing_sources = ["baseline"]

    return {
        "date": chosen_date,
        "timing": timing,
        "reason": reason,
        "confidence": confidence,
        "date_sources": date_sources,
        "timing_sources": timing_sources,
        "fiscal_year": (
            announcement.get("fiscal_year")
            if announcement is not None
            else None
        ),
        "fiscal_q": (
            announcement.get("fiscal_q")
            if announcement is not None
            else None
        ),
    }


def _safe_error(response: requests.Response, secret: str) -> str:
    text = (response.text or "").strip().replace(secret, "[redacted]")
    text = re.sub(r"(?i)(apikey|api_key|token)=[^&\s]+", r"\1=[redacted]", text)
    return text[:300]


def _request_json(
    url: str,
    *,
    params: dict[str, Any],
    secret: str,
    headers: dict[str, str] | None = None,
    retries: int = 2,
) -> tuple[int, Any | None, str | None]:
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=60,
            )
        except requests.RequestException as exc:
            if attempt == retries:
                return 0, None, type(exc).__name__
            time.sleep(1 + attempt)
            continue
        if response.status_code == 429 and attempt < retries:
            time.sleep(8 * (attempt + 1))
            continue
        if not response.ok:
            return response.status_code, None, _safe_error(response, secret)
        try:
            return response.status_code, response.json(), None
        except ValueError:
            return response.status_code, None, "invalid_json"
    return 0, None, "request_failed"


def _request_text(
    url: str,
    *,
    params: dict[str, Any],
    secret: str,
) -> tuple[int, str | None, str | None]:
    try:
        response = requests.get(url, params=params, timeout=60)
    except requests.RequestException as exc:
        return 0, None, type(exc).__name__
    if not response.ok:
        return response.status_code, None, _safe_error(response, secret)
    return response.status_code, response.text, None


def _env_key(name: str) -> str | None:
    return os.getenv(name) or None


def _parse_iso_day(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    compact = re.match(r"(20\d{2})(\d{2})(\d{2})", raw)
    if compact:
        try:
            return date(
                int(compact.group(1)),
                int(compact.group(2)),
                int(compact.group(3)),
            )
        except ValueError:
            return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _vote(provider: str, symbol: str, event_date: Any, timing: Any = None) -> Vote | None:
    try:
        parsed = date.fromisoformat(str(event_date)[:10])
    except ValueError:
        return None
    return {
        "provider": provider,
        "symbol": str(symbol or "").upper(),
        "date": parsed.isoformat(),
        "timing": normalize_timing(timing),
    }


def fetch_finnhub_calendar(
    start: date,
    end: date,
    key: str | None,
) -> tuple[list[Vote], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    status, payload, error = _request_json(
        "https://finnhub.io/api/v1/calendar/earnings",
        params={"from": start.isoformat(), "to": end.isoformat(), "token": key},
        secret=key,
    )
    if payload is None:
        return [], {"status": "error", "http_status": status, "error": error}
    rows = payload.get("earningsCalendar") if isinstance(payload, dict) else []
    votes = [
        vote
        for row in rows or []
        if isinstance(row, dict)
        for vote in [_vote("finnhub_calendar", row.get("symbol"), row.get("date"), row.get("hour"))]
        if vote is not None and start <= date.fromisoformat(vote["date"]) <= end
    ]
    return votes, {"status": "ok", "http_status": status, "rows": len(votes)}


def fetch_alphavantage_calendar(
    start: date,
    end: date,
    key: str | None,
) -> tuple[list[Vote], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    status, text, error = _request_text(
        "https://www.alphavantage.co/query",
        params={
            "function": "EARNINGS_CALENDAR",
            "horizon": "3month",
            "apikey": key,
        },
        secret=key,
    )
    if text is None:
        return [], {"status": "error", "http_status": status, "error": error}
    if text.lstrip().startswith("{"):
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {}
        message = body.get("Information") or body.get("Note") or body.get("Error Message")
        return [], {
            "status": "error",
            "http_status": status,
            "error": str(message or "json_error_response")[:300],
        }

    votes: list[Vote] = []
    for row in csv.DictReader(io.StringIO(text)):
        vote = _vote(
            "alphavantage_calendar",
            row.get("symbol"),
            row.get("reportDate") or row.get("date"),
        )
        if vote is not None and start <= date.fromisoformat(vote["date"]) <= end:
            votes.append(vote)
    return votes, {"status": "ok", "http_status": status, "rows": len(votes)}


def fetch_fmp_calendar(
    start: date,
    end: date,
    key: str | None,
) -> tuple[list[Vote], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    status, payload, error = _request_json(
        "https://financialmodelingprep.com/stable/earnings-calendar",
        params={"apikey": key},
        secret=key,
    )
    if payload is None:
        return [], {"status": "error", "http_status": status, "error": error}
    if not isinstance(payload, list):
        return [], {
            "status": "error",
            "http_status": status,
            "error": "unexpected_response_shape",
        }

    votes: list[Vote] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        timing = next(
            (
                row.get(field)
                for field in (
                    "time",
                    "reportTime",
                    "reportingTime",
                    "estimatedReportTime",
                )
                if row.get(field) not in (None, "")
            ),
            None,
        )
        vote = _vote("fmp_calendar", row.get("symbol"), row.get("date"), timing)
        if vote is not None and start <= date.fromisoformat(vote["date"]) <= end:
            votes.append(vote)
    return votes, {"status": "ok", "http_status": status, "rows": len(votes)}


def _announcement_from_article(
    *,
    provider: str,
    title: str,
    body: str,
    published_on: date | None,
    official: bool,
    url: str | None,
) -> Announcement | None:
    if published_on is None or not is_direct_earnings_announcement_title(title):
        return None
    parsed = extract_earnings_announcement(
        f"{title}. {body}",
        published_on=published_on,
    )
    if parsed is None:
        return None
    fiscal_identity = extract_fiscal_identity(f"{title}. {body}")
    return {
        "provider": provider,
        "date": parsed["date"],
        "timing": parsed["timing"],
        "published_on": published_on.isoformat(),
        "official": official,
        "direct": True,
        "title": _clean_text(title)[:180],
        "url": url,
        **fiscal_identity,
    }


def fetch_finnhub_announcements(
    symbol: str,
    *,
    today: date,
    key: str | None,
) -> tuple[list[Announcement], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    status, payload, error = _request_json(
        "https://finnhub.io/api/v1/company-news",
        params={
            "symbol": symbol,
            "from": (today - timedelta(days=120)).isoformat(),
            "to": today.isoformat(),
            "token": key,
        },
        secret=key,
    )
    if payload is None:
        return [], {"status": "error", "http_status": status, "error": error}
    rows = payload if isinstance(payload, list) else []
    evidence: list[Announcement] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _announcement_from_article(
            provider="finnhub_company_news",
            title=str(row.get("headline") or ""),
            body=str(row.get("summary") or ""),
            published_on=_parse_iso_day(row.get("datetime")),
            official=False,
            url=row.get("url"),
        )
        if item:
            evidence.append(item)
    return evidence, {
        "status": "ok",
        "http_status": status,
        "articles": len(rows),
        "evidence": len(evidence),
    }


def fetch_twelvedata_announcements(
    symbol: str,
    *,
    today: date,
    key: str | None,
) -> tuple[list[Announcement], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    status, payload, error = _request_json(
        "https://api.twelvedata.com/press_releases",
        params={
            "symbol": symbol,
            "start_date": f"{(today - timedelta(days=120)).isoformat()}T00:00:00",
            "end_date": f"{today.isoformat()}T23:59:59",
            "timezone": "America/New_York",
            "language": "en,en-US",
            "outputsize": 10,
            "page": 1,
            "apikey": key,
        },
        secret=key,
        retries=3,
    )
    if payload is None or (
        isinstance(payload, dict) and payload.get("status") == "error"
    ):
        provider_error = (
            str(payload.get("message"))[:300]
            if isinstance(payload, dict)
            else error
        )
        return [], {
            "status": "error",
            "http_status": status,
            "error": provider_error,
        }
    rows = payload.get("press_releases") if isinstance(payload, dict) else []
    evidence: list[Announcement] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = _announcement_from_article(
            provider="twelvedata_press_release",
            title=str(row.get("title") or ""),
            body=str(row.get("body") or ""),
            published_on=_parse_iso_day(row.get("datetime")),
            official=True,
            url=None,
        )
        if item:
            evidence.append(item)
    return evidence, {
        "status": "ok",
        "http_status": status,
        "articles": len(rows or []),
        "evidence": len(evidence),
    }


def fetch_alphavantage_announcements(
    symbol: str,
    *,
    today: date,
    key: str | None,
) -> tuple[list[Announcement], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    status, payload, error = _request_json(
        "https://www.alphavantage.co/query",
        params={
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "topics": "earnings",
            "time_from": (today - timedelta(days=120)).strftime("%Y%m%dT0000"),
            "time_to": today.strftime("%Y%m%dT2359"),
            "sort": "LATEST",
            "limit": 50,
            "apikey": key,
        },
        secret=key,
    )
    if payload is None:
        return [], {"status": "error", "http_status": status, "error": error}
    if isinstance(payload, dict) and (
        payload.get("Information") or payload.get("Note") or payload.get("Error Message")
    ):
        message = payload.get("Information") or payload.get("Note") or payload.get("Error Message")
        return [], {
            "status": "error",
            "http_status": status,
            "error": str(message)[:300],
        }
    rows = payload.get("feed") if isinstance(payload, dict) else []
    evidence: list[Announcement] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if not alpha_article_mentions_symbol(row, symbol):
            continue
        item = _announcement_from_article(
            provider="alphavantage_news",
            title=str(row.get("title") or ""),
            body=str(row.get("summary") or ""),
            published_on=_parse_iso_day(row.get("time_published")),
            official=False,
            url=row.get("url"),
        )
        if item:
            evidence.append(item)
    return evidence, {
        "status": "ok",
        "http_status": status,
        "articles": len(rows or []),
        "evidence": len(evidence),
    }


def _load_calendar(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return normalize_existing(pd.read_csv(path, keep_default_na=False))


def _row_vote(row: pd.Series | dict[str, Any], provider: str) -> Vote:
    source_parts = {
        part.strip()
        for part in str(row.get("source") or "").split("+")
        if part.strip()
    }
    confidence = (
        "confirmed"
        if source_parts.intersection(
            {
                "override",
                "twelvedata_press_release",
                "finnhub_company_news",
                "alphavantage_news",
            }
        )
        else "projected"
    )
    return {
        "provider": provider,
        "date": str(row["date"])[:10],
        "timing": normalize_timing(row.get("timing")),
        "confidence": confidence,
    }


def _rows_in_window(
    df: pd.DataFrame,
    symbol: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    if df.empty:
        return df
    mask = (
        df["act_symbol"].eq(symbol)
        & df["date"].map(lambda value: start <= value <= end)
    )
    return df.loc[mask].copy()


def _pick_anchor_row(
    rows: pd.DataFrame,
    *,
    preferred_date: date | None = None,
) -> pd.Series | None:
    if rows.empty:
        return None
    if preferred_date is not None:
        ordered = rows.assign(
            _distance=rows["date"].map(lambda value: abs((value - preferred_date).days))
        ).sort_values(["_distance", "date"])
        return ordered.iloc[0]
    return rows.sort_values("date").iloc[0]


def _provider_votes_by_symbol(votes: list[Vote]) -> dict[str, list[Vote]]:
    out: dict[str, list[Vote]] = defaultdict(list)
    for vote in votes:
        symbol = str(vote.get("symbol") or "").upper()
        if symbol:
            out[symbol].append(vote)
    return out


def _closest_vote(
    votes: list[Vote],
    *,
    anchor: date | None,
) -> Vote | None:
    if not votes:
        return None
    if anchor is None:
        return min(votes, key=lambda row: row["date"])
    return min(
        votes,
        key=lambda row: (
            abs((date.fromisoformat(row["date"]) - anchor).days),
            row["date"],
        ),
    )


def _append_source(source: Any, *parts: str) -> str:
    existing = [
        item
        for item in str(source or "").split("+")
        if item and item.lower() != "nan"
    ]
    for part in parts:
        if part and part not in existing:
            existing.append(part)
    return "+".join(existing) or "reconciled"


def _priority_symbols_for_alpha(
    symbols: list[str],
    decisions_without_alpha: dict[str, dict[str, Any]],
    max_symbols: int,
) -> list[str]:
    def priority(symbol: str) -> tuple[int, str]:
        decision = decisions_without_alpha.get(symbol) or {}
        reason = decision.get("reason")
        rank = {
            "baseline_sticky": 0,
            "uncorroborated_new_event": 1,
            "current_base": 2,
            "structured_consensus": 3,
            "direct_announcement": 4,
            "official_announcement": 5,
            "baseline_confirmed": 6,
        }.get(str(reason), 7)
        return rank, symbol

    return sorted(symbols, key=priority)[:max_symbols]


def reconcile_calendar(
    current_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    *,
    start: date,
    end: date,
    structured_votes: list[Vote],
    announcements_by_symbol: dict[str, list[Announcement]],
    allowed_symbols: set[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    current_df = normalize_existing(current_df)
    baseline_df = normalize_existing(baseline_df)

    provider_map = _provider_votes_by_symbol(structured_votes)
    symbols: set[str] = set(
        current_df.loc[
            current_df["date"].map(lambda value: start <= value <= end),
            "act_symbol",
        ].tolist()
    )
    symbols.update(
        baseline_df.loc[
            baseline_df["date"].map(lambda value: start <= value <= end),
            "act_symbol",
        ].tolist()
    )
    symbols.update(provider_map)
    symbols.update(announcements_by_symbol)
    if allowed_symbols is not None:
        symbols.intersection_update(allowed_symbols)

    drop_indices: set[int] = set()
    replacement_rows: list[dict[str, Any]] = []
    decisions: dict[str, Any] = {}

    for symbol in sorted(symbols):
        current_rows = _rows_in_window(current_df, symbol, start, end)
        baseline_rows = _rows_in_window(baseline_df, symbol, start, end)

        baseline_row = _pick_anchor_row(baseline_rows)
        baseline_date = baseline_row["date"] if baseline_row is not None else None
        current_row = _pick_anchor_row(current_rows, preferred_date=baseline_date)

        current_vote = (
            _row_vote(current_row, "dolthub_calendar")
            if current_row is not None
            else None
        )
        baseline_vote = (
            _row_vote(baseline_row, "baseline")
            if baseline_row is not None
            else None
        )

        anchor = (
            current_row["date"]
            if current_row is not None
            else baseline_row["date"]
            if baseline_row is not None
            else None
        )
        votes: list[Vote] = []
        if current_vote is not None:
            votes.append(current_vote)
        by_provider: dict[str, list[Vote]] = defaultdict(list)
        for vote in provider_map.get(symbol, []):
            by_provider[str(vote["provider"])].append(vote)
        for provider_votes in by_provider.values():
            selected = _closest_vote(provider_votes, anchor=anchor)
            if selected:
                votes.append(selected)

        decision = choose_canonical_event(
            current=current_vote,
            baseline=baseline_vote,
            structured_votes=votes,
            announcements=announcements_by_symbol.get(symbol, []),
            as_of=start,
        )
        decision["structured_votes"] = [
            {
                "provider": vote["provider"],
                "date": vote["date"],
                "timing": vote["timing"],
            }
            for vote in _distinct_votes(votes)
        ]
        decision["announcement_evidence"] = [
            {
                key: row.get(key)
                for key in (
                    "provider",
                    "date",
                    "timing",
                    "published_on",
                    "official",
                    "direct",
                    "title",
                    "url",
                    "fiscal_year",
                    "fiscal_q",
                )
            }
            for row in announcements_by_symbol.get(symbol, [])
        ]
        decisions[symbol] = decision

        chosen_date_text = decision.get("date")
        if not chosen_date_text:
            continue
        chosen_date = date.fromisoformat(str(chosen_date_text))

        if current_row is not None:
            replacement = current_row.to_dict()
        elif baseline_row is not None:
            replacement = baseline_row.to_dict()
        else:
            replacement = {column: None for column in OUTPUT_COLUMNS}
            replacement["act_symbol"] = symbol
            replacement["fiscal_year"] = (
                decision.get("fiscal_year") or chosen_date.year
            )
            replacement["fiscal_q"] = (
                decision.get("fiscal_q") or fiscal_q_from_date(chosen_date)
            )

        if decision.get("fiscal_year") is not None:
            replacement["fiscal_year"] = decision["fiscal_year"]
        if decision.get("fiscal_q"):
            replacement["fiscal_q"] = decision["fiscal_q"]
        replacement["date"] = chosen_date
        replacement["timing"] = decision["timing"]
        replacement["source"] = _append_source(
            replacement.get("source"),
            "reconciled",
            *decision.get("date_sources", []),
        )

        # The selected current row is the event identity being reconciled.
        # Always replace that anchor, even when an announcement moves the event
        # by more than the nearby-duplicate cleanup window.
        if current_row is not None:
            drop_indices.add(int(current_row.name))
        for index, row in current_rows.iterrows():
            if abs((row["date"] - chosen_date).days) <= 35:
                drop_indices.add(int(index))
        replacement_rows.append(replacement)

    kept = current_df.drop(index=list(drop_indices), errors="ignore")
    if replacement_rows:
        replacements = pd.DataFrame(replacement_rows, columns=OUTPUT_COLUMNS)
        merged = pd.concat([kept, replacements], ignore_index=True)
    else:
        merged = kept
    merged = normalize_existing(merged)
    merged = merged.sort_values(["date", "act_symbol"]).reset_index(drop=True)

    changed = {
        symbol: decision
        for symbol, decision in decisions.items()
        if decision.get("reason") not in {"current_base", "no_candidate"}
    }
    return merged, {
        "symbols_evaluated": len(decisions),
        "decisions": decisions,
        "nontrivial_decisions": changed,
    }


def _candidate_symbols(
    current_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    provider_votes: list[Vote],
    *,
    start: date,
    end: date,
    allowed_symbols: set[str] | None = None,
    max_symbols: int | None = None,
) -> list[str]:
    nearest: dict[str, date] = {}

    def add(symbol: Any, event_date: date) -> None:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return
        if allowed_symbols is not None and normalized not in allowed_symbols:
            return
        prior = nearest.get(normalized)
        if prior is None or abs((event_date - start).days) < abs((prior - start).days):
            nearest[normalized] = event_date

    for df in (current_df, baseline_df):
        if df.empty:
            continue
        for _, row in df.iterrows():
            event_date = row.get("date")
            if not isinstance(event_date, date) or not start <= event_date <= end:
                continue
            add(row.get("act_symbol"), event_date)

    for vote in provider_votes:
        try:
            vote_date = date.fromisoformat(str(vote["date"]))
        except (TypeError, ValueError):
            continue
        if start <= vote_date <= end:
            add(vote.get("symbol"), vote_date)

    ordered = sorted(
        nearest,
        key=lambda symbol: (abs((nearest[symbol] - start).days), symbol),
    )
    if max_symbols is not None:
        ordered = ordered[:max_symbols]
    return ordered


def _collect_announcements(
    symbols: list[str],
    *,
    today: date,
    finnhub_key: str | None,
    twelvedata_key: str | None,
    alphavantage_key: str | None,
    finnhub_delay: float,
    twelvedata_delay: float,
    alphavantage_delay: float,
    alpha_news_max: int,
    preliminary_decisions: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, list[Announcement]], dict[str, Any]]:
    evidence: dict[str, list[Announcement]] = defaultdict(list)
    status: dict[str, Any] = {
        "finnhub_company_news": {},
        "twelvedata_press_releases": {},
        "alphavantage_news": {},
    }

    for index, symbol in enumerate(symbols):
        rows, meta = fetch_finnhub_announcements(
            symbol,
            today=today,
            key=finnhub_key,
        )
        evidence[symbol].extend(rows)
        status["finnhub_company_news"][symbol] = meta
        if index + 1 < len(symbols) and finnhub_key:
            time.sleep(finnhub_delay)

    for index, symbol in enumerate(symbols):
        rows, meta = fetch_twelvedata_announcements(
            symbol,
            today=today,
            key=twelvedata_key,
        )
        evidence[symbol].extend(rows)
        status["twelvedata_press_releases"][symbol] = meta
        if index + 1 < len(symbols) and twelvedata_key:
            time.sleep(twelvedata_delay)

    alpha_symbols = symbols
    if preliminary_decisions is not None:
        alpha_symbols = _priority_symbols_for_alpha(
            symbols,
            preliminary_decisions,
            alpha_news_max,
        )
    else:
        alpha_symbols = symbols[:alpha_news_max]
    for index, symbol in enumerate(alpha_symbols):
        rows, meta = fetch_alphavantage_announcements(
            symbol,
            today=today,
            key=alphavantage_key,
        )
        evidence[symbol].extend(rows)
        status["alphavantage_news"][symbol] = meta
        if index + 1 < len(alpha_symbols) and alphavantage_key:
            time.sleep(alphavantage_delay)

    return dict(evidence), status


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--days-ahead", type=int, default=60)
    parser.add_argument("--announcement-days-ahead", type=int, default=35)
    parser.add_argument("--finnhub-delay", type=float, default=1.05)
    parser.add_argument("--twelvedata-delay", type=float, default=8.0)
    parser.add_argument("--alphavantage-delay", type=float, default=1.1)
    parser.add_argument("--alpha-news-max", type=int, default=20)
    parser.add_argument("--announcement-max-symbols", type=int, default=80)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.days_ahead < 1:
        parser.error("--days-ahead must be positive")
    if not 1 <= args.announcement_days_ahead <= args.days_ahead:
        parser.error("--announcement-days-ahead must be between 1 and --days-ahead")
    if args.alpha_news_max < 0:
        parser.error("--alpha-news-max cannot be negative")
    if args.announcement_max_symbols < 1:
        parser.error("--announcement-max-symbols must be positive")

    today = et_today()
    end = today + timedelta(days=args.days_ahead)
    announcement_end = today + timedelta(days=args.announcement_days_ahead)
    data_dir = args.data_dir or default_data_dir()

    current_path = data_dir / "earnings_calendar.csv"
    current_df = _load_calendar(current_path)
    baseline_df = _load_calendar(args.baseline)
    if current_df.empty:
        print(f"earnings calendar is empty: {current_path}", file=sys.stderr)
        return 1

    finnhub_key = _env_key("FINNHUB_API_KEY")
    fmp_key = _env_key("FMP_API_KEY")
    alphavantage_key = _env_key("ALPHAVANTAGE_API_KEY")
    twelvedata_key = _env_key("TWELVEDATA_API_KEY")

    provider_votes: list[Vote] = []
    provider_status: dict[str, Any] = {}

    finnhub_votes, provider_status["finnhub_calendar"] = fetch_finnhub_calendar(
        today,
        end,
        finnhub_key,
    )
    provider_votes.extend(finnhub_votes)

    alpha_votes, provider_status["alphavantage_calendar"] = fetch_alphavantage_calendar(
        today,
        end,
        alphavantage_key,
    )
    provider_votes.extend(alpha_votes)

    fmp_votes, provider_status["fmp_calendar"] = fetch_fmp_calendar(
        today,
        end,
        fmp_key,
    )
    provider_votes.extend(fmp_votes)

    frontend_symbols = set(load_frontend_symbol_universe())
    allowed_symbols = frontend_symbols or None

    preliminary, preliminary_report = reconcile_calendar(
        current_df,
        baseline_df,
        start=today,
        end=end,
        structured_votes=provider_votes,
        announcements_by_symbol={},
        allowed_symbols=allowed_symbols,
    )
    del preliminary

    symbols = _candidate_symbols(
        current_df,
        baseline_df,
        provider_votes,
        start=today,
        end=announcement_end,
        allowed_symbols=allowed_symbols,
        max_symbols=args.announcement_max_symbols,
    )
    announcements, announcement_status = _collect_announcements(
        symbols,
        today=today,
        finnhub_key=finnhub_key,
        twelvedata_key=twelvedata_key,
        alphavantage_key=alphavantage_key,
        finnhub_delay=args.finnhub_delay,
        twelvedata_delay=args.twelvedata_delay,
        alphavantage_delay=args.alphavantage_delay,
        alpha_news_max=args.alpha_news_max,
        preliminary_decisions=preliminary_report["decisions"],
    )

    reconciled, reconciliation = reconcile_calendar(
        current_df,
        baseline_df,
        start=today,
        end=end,
        structured_votes=provider_votes,
        announcements_by_symbol=announcements,
        allowed_symbols=allowed_symbols,
    )

    report = {
        "schema": "quantiv.earnings-calendar-reconciliation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "start": today.isoformat(),
            "end": end.isoformat(),
            "announcement_end": announcement_end.isoformat(),
        },
        "provider_status": provider_status,
        "announcement_status": announcement_status,
        **reconciliation,
    }
    _write_report(args.report, report)

    reason_counts = Counter(
        decision.get("reason")
        for decision in reconciliation["decisions"].values()
    )
    print(
        "earnings reconciliation: "
        + ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(reason_counts.items())
        )
    )
    print(
        f"announcement symbols={len(symbols)}; "
        f"provider votes={len(provider_votes)}; "
        f"rows={len(current_df)}->{len(reconciled)}"
    )

    if args.dry_run:
        print(f"dry run: report written to {args.report}; calendar unchanged")
        return 0

    write_outputs(reconciled, data_dir)
    print(f"wrote {data_dir / 'earnings_calendar.csv'}")
    print(f"wrote {data_dir / 'earnings_calendar.parquet'}")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
