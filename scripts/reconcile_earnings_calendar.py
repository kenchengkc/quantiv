#!/usr/bin/env python3
"""Conservatively reconcile near-term earnings dates across independent sources.

Production contract:
- DoltHub supplies the base calendar/history.
- Prior published calendar is sticky when providers disagree.
- A single structured vendor cannot move an existing event.
- Two independent structured sources may corroborate a new date.
- Official/direct company announcements may override estimates.
- Recent completed reports can repair nearby unconfirmed future projections.
- Verified share classes share event evidence, never financials or forecasts.
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
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import calendar_announcement_state as announcement_state

from sync_finnhub_earnings import (
    OUTPUT_COLUMNS,
    default_data_dir,
    et_today,
    fiscal_q_from_date,
    load_data_backed_symbol_universe,
    load_frontend_symbol_universe,
    normalize_existing,
    write_outputs,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "data" / "validation" / "earnings_calendar_baseline.csv"
DEFAULT_REPORT = REPO_ROOT / "data" / "validation" / "earnings_calendar_reconciliation.json"
TICKER_NAMES_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-names.json"
MAX_NEW_ANNOUNCEMENT_SYMBOLS = 100
SHARE_CLASSES_PATH = REPO_ROOT / "config" / "earnings_share_classes.json"
COMPLETED_LOOKBACK_DAYS = 14
EVENT_CORRECTION_DAYS = 35

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
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
MONTHS["sept"] = 9
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_RE = re.compile(
    rf"\b({MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
    re.IGNORECASE,
)
WEEKDAY_PREFIX = r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
TIME_ZONE_PATTERN = (
    r"(?:Eastern|Central|Mountain|Pacific)(?:\s+(?:Standard|Daylight))?\s+Time|"
    r"EST|EDT|CST|CDT|MST|MDT|PST|PDT|ET|CT|MT|PT"
)
TIME_RE = re.compile(
    # A clock without AM/PM must include both minutes and a supported zone.
    # The lookahead prevents ordinary dates/numbers from becoming clock matches.
    r"(?<![\d:])\b(?=\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?|"
    rf"\d{{1,2}}:\d{{2}}\s*(?:\(\s*)?(?:{TIME_ZONE_PATTERN})(?!\w))"
    r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*"
    r"(?:(?P<zone_open>\()\s*)?"
    rf"(?P<zone>{TIME_ZONE_PATTERN})?(?!\w)"
    r"(?(zone_open)\s*\))",
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
HOST_TITLE_ACTIONS = (
    " to host ", " will host ", " plans live audio webcast ",
    " to webcast ", " conference call to discuss ",
)
DIRECT_TITLE_ACTIONS = (
    " to announce ",
    " to report ",
    " to release ",
    " to hold ",
    " announces ",
    " schedules ",
    " scheduled ",
    " reschedules ",
    " sets date ",
    " will announce ",
    " will report ",
    " will release ",
    *HOST_TITLE_ACTIONS,
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
DIRECT_TITLE_PROJECTED = (
    " projected ",
    " estimated ",
    " estimate ",
    " expected ",
    " expects ",
    " forecast ",
    " forecasts ",
    " likely ",
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
    text = re.sub(r"<script\b[^>]*>.*?</script\b[^>]*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style\b[^>]*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_direct_earnings_announcement_title(title: str) -> bool:
    text = f" {_clean_text(title).lower()} "
    if any(term in text for term in DIRECT_TITLE_PAST):
        return False
    if any(term in text for term in DIRECT_TITLE_PROJECTED):
        return False
    # A planned earnings release is a scheduling notice; general corporate
    # plans or an earnings preview are not. Call/discussion titles still need
    # a separate release statement in the article body before admission.
    plans_release = bool(re.search(r"\bplans\b[^.!?]{0,100}\b(?:earnings|financial results|results) release\b", text))
    if (" conference call to discuss " in text
            and not re.search(r"\b(?:earnings|results)\b", text)):
        return False
    if not plans_release and not any(term in text for term in DIRECT_TITLE_ACTIONS):
        return False
    if any(term in text for term in DIRECT_TITLE_RESULTS):
        return True
    quarter_context = re.search(
        r"\b(?:first|second|third|fourth)[\s-]+quarter\b|\bq[1-4]\b|\b[1-4]q(?:\d{2}|20\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    return bool(quarter_context and " results " in text)


def extract_fiscal_identity(text: str) -> dict[str, Any]:
    cleaned = _clean_text(text)

    patterns: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(
                r"\b(20\d{2})\s+(first|second|third|fourth)\s+quarter\b",
                re.IGNORECASE,
            ),
            "year_first",
        ),
        (
            re.compile(
                r"\b(first|second|third|fourth)\s+quarter"
                r"(?:\s+(?:of\s+)?)?(?:fiscal(?:\s+year)?\s*)?(20\d{2})\b",
                re.IGNORECASE,
            ),
            "quarter_first",
        ),
        (
            re.compile(r"\bfy\s*(20\d{2})\s*q([1-4])\b", re.IGNORECASE),
            "fy_q",
        ),
        (
            re.compile(r"\bq([1-4])\s*(?:fy\s*)?(20\d{2})\b", re.IGNORECASE),
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
        r"\b(first|second|third|fourth)\s+quarter\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if quarter_match:
        nearby = cleaned[
            quarter_match.start() : min(len(cleaned), quarter_match.end() + 60)
        ]
        year_match = re.search(
            r"\bfiscal(?:\s+year)?\s+(20\d{2})\b",
            nearby,
            flags=re.IGNORECASE,
        )
        if year_match:
            return {
                "fiscal_year": int(year_match.group(1)),
                "fiscal_q": QUARTER_WORDS[quarter_match.group(1).lower()],
            }

    return {}


def _session_dates_match(text: str, *, report_day: date) -> bool:
    # A dateline before the announcement and the fiscal period being reported
    # are not event dates. Every other date in the timing clause must agree.
    parts = re.split(r"\s(—|–|--)\s", text, maxsplit=1)
    dateline = DATE_RE.search(parts[0]) if len(parts) == 3 else None
    if (
        dateline
        # En dashes also delimit date ranges. Require a city prefix for that
        # spelling, rather than treating a bare/weekday date as a dateline.
        and (parts[1] != "–" or (
            bool(parts[0][:dateline.start()].strip())
            and not re.fullmatch(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*",
                                 parts[0][:dateline.start()], re.I)
        ))
        and re.fullmatch(r"(?:[a-z][a-z .'-]*,\s*)*", parts[0][:dateline.start()], re.I)
        and re.fullmatch(
            r"\s*(?:(?:/PRNewswire/|\(GLOBE NEWSWIRE\))\s*)?",
            parts[0][dateline.end():], re.I,
        )
    ):
        text = parts[2]
    for item in DATE_RE.finditer(text):
        if re.search(
            r"\b(?:quarter|year|period|months?)\s+end(?:ed|ing|s)?\s+(?:on\s+)?"
            r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?$",
            text[max(0, item.start() - 80):item.start()], re.I,
        ):
            continue
        if _candidate_date(item, report_day) != report_day:
            return False
    return True


def _call_bound_spans_another_day(text: str) -> bool:
    return bool(re.search(
        r"\b(?:evening|night|afternoon|yesterday|tomorrow)\b|"
        r"\b(?:previous|prior|preceding|following|next)\s+(?:business\s+)?(?:day|morning)\b|"
        r"\bdays?\s+(?:before|prior to)\b",
        text, re.I,
    ))


def _cross_sentence_release_call_clock(text: str, *, report_day: date) -> int | None:
    """Locate one explicitly bounded call clock across three adjacent sentences."""
    clocks = list(TIME_RE.finditer(text))
    if len(clocks) != 1:
        return None
    clock = clocks[0]
    # Mask the complete clock so "a.m. Eastern Time" is not a sentence break.
    marked = text[:clock.start()] + "release_call_clock" + text[clock.end():]
    sentences = re.split(r"[.!?;]\s+(?=[A-Z])", marked)
    for index in range(1, len(sentences) - 1):
        if not re.fullmatch(
            r"\s*(?:the|this) call will (?:begin|start) at release_call_clock\s*",
            sentences[index], re.I,
        ):
            continue
        call, release = sentences[index - 1], sentences[index + 1]
        if re.search(r"\b(?:replay|transcript|recording)\b", call + " " + release, re.I):
            continue
        if _call_bound_spans_another_day(release):
            continue
        action = re.search(
            r"\bwill\s+(?:host|hold|conduct)\s+(?:(?:a|an|the|its)\s+)?"
            r"(?:earnings\s+)?(?:conference\s+)?call\b", call, re.I,
        )
        if action is None:
            continue
        call_dates = {_candidate_date(item, report_day)
                      for item in DATE_RE.finditer(call[action.end():])}
        if call_dates != {report_day}:
            continue
        if any(_candidate_date(item, report_day) != report_day for item in DATE_RE.finditer(release)):
            continue
        if re.search(
            r"\bwill\s+(?:release|issue|publish|post|report|announce)\b.{0,120}"
            r"\b(?:financial results|results|earnings (?:report|release))\b.{0,80}"
            r"\b(?:before|prior to)\s+the\s+(?:conference\s+)?call\b",
            release, re.I,
        ):
            return clock.start()
    return None


def _session_from_text(
    text: str, *, report_day: date, allow_cross_sentence: bool = True,
) -> str:
    # Only release language may supply a session; a later call has a different
    # timestamp and can even occur on the following day.
    market = r"(?:the\s+)?(?:(?:u\.?s\.?|us)\s+)?(?:financial\s+|stock\s+)?(?:markets?|nyse|nasdaq(?:\s+global(?:\s+select)?\s+market)?|new york stock exchange)"
    session_patterns = {
        "bmo": [rf"(?:before|prior to)\s+{market}\s+opens?",
                rf"(?:before|prior to)\s+(?:the\s+)?opening(?:\s+of trading)?\s+(?:of|on)\s+{market}",
                r"\bbefore\s+(?:the\s+)?market(?=\s+on\b|[.,;]|$)",
                r"pre-market|premarket|(?:issued|released|release) that morning"],
        "amc": [rf"(?:after|following)\s+{market}\s+closes?",
                r"(?:after|following) (?:the )?close(?: of (?:regular )?(?:stock )?market trading)?",
                r"post-market|postmarket"],
    }
    found = set()
    for session, patterns in session_patterns.items():
        for match in re.finditer("|".join(patterns), text, flags=re.I):
            prefix = re.split(r"[.!?;]\s+(?=[A-Z])", text[:match.end()])[-1].lower()
            suffix = re.split(r"[.!?;]\s+(?=[A-Z])", text[match.end():], maxsplit=1)[0].lower()
            if not _session_dates_match(prefix + suffix, report_day=report_day):
                continue
            # A conjunction before the session starts a new clause: "release
            # results and, after close, host a call" does not time the release.
            if re.search(r"\band\s*,?\s*$", text[:match.start()].lower()):
                prefix = ""
            if re.match(r"[\s,]*(?:host|hold|conduct)\b", suffix):
                continue
            # Require an action, not the noun "earnings release" in a sentence
            # describing its discussion. Support "Before market open ... will
            # release" as well as the usual action-before-session wording.
            action = r"\b(?:(?:will|to)\s+(?:\w+\s+){0,2}(?:post|release|issue|publish|report|announce)|issued|released|published|posted|reported|announced|reports|announces)\b"
            releases = list(re.finditer(action, prefix))
            calls = list(re.finditer(r"\b(?:call|conference|webcast|interview|discuss|discussed)\b", prefix))
            before = releases and (not calls or releases[-1].start() > calls[-1].start())
            following = re.search(action, suffix)
            before_action = suffix[:following.start()] if following else ""
            if following and re.match(
                r"\s+(?:(?:its|the|a|copy|of)\s+){0,5}"
                r"(?:financial results|results|(?:earnings\s+)?press release|earnings release)\b",
                suffix[following.end():],
            ):
                # "After close and prior to the conference call, ... will issue
                # the earnings release" explicitly times publication. Removing
                # only the subordinate bound keeps call-hosting clauses excluded.
                before_action = re.sub(
                    r"\b(?:before|prior to)\s+the\s+(?:conference\s+)?call\b",
                    "",
                    before_action,
                )
            after = following and not calls and not re.search(
                r"\b(?:call|conference|webcast|interview|discuss|discussed)\b", before_action)
            if before or after:
                found.add(session)
    if len(found) == 1:
        return found.pop()
    if len(found) > 1:
        return "unknown"

    lower = text.lower()
    mentions_call = bool(re.search(r"\b(?:call|conference|webcast|interview)\b", lower))
    clock_clause = re.compile(
        r"\b(?:and|while|but)\s+(?=(?:(?:the\s+)?(?:company|management|it)\s+)?"
        r"(?:will\s+)?(?:hold|host|conduct)\b)",
        re.I,
    )
    release_object = r"\b(?:results|earnings|press release|materials)\b"
    release_clock_action = re.compile(
        r"\b(?:(?:will|to)\s+(?:(?:also|publicly|electronically|formally)\s+){0,2}"
        r"(?:post|release|issue|publish|report|announce)|"
        r"posts|releases|issues|publishes|reports|announces)\b"
        r"[^.!?]{0,120}?" + release_object + "|" + release_object +
        r"[^.!?]{0,120}?\b(?:will be|to be|are|is)\s+(?:made\s+)?"
        r"(?:released|issued|published|posted|reported|announced|available)\b",
        re.I,
    )
    bounded_call_clock = (
        _cross_sentence_release_call_clock(text, report_day=report_day)
        if allow_cross_sentence else None
    )
    times: list[int] = []
    for match in TIME_RE.finditer(text):
        # A call time alone is not the release time. An explicit release clock
        # in its own sentence remains useful when a later sentence mentions a call.
        prefix = re.split(r"[.!?;]\s+(?=[A-Z])", text[:match.start()])[-1].lower()
        # A following call clause must not invalidate an explicit release
        # clock. Split only call-hosting actions; publication of a replay or
        # transcript must retain the preceding call context.
        prefix = clock_clause.split(prefix)[-1]
        if re.search(r"\b(?:replay|transcript|recording)\b", prefix):
            continue
        release_clock = bool(release_clock_action.search(prefix))
        suffix = re.split(r"[.!?;]\s+(?=[A-Z])", text[match.end():], maxsplit=1)[0].lower()
        suffix = clock_clause.split(suffix, maxsplit=1)[0]
        clause = prefix + " " + suffix
        if not _session_dates_match(clause, report_day=report_day):
            continue
        call_clock = bool(re.search(r"\b(?:call|conference|webcast|interview)\b", clause))
        release_linked_to_call = bool(re.search(
            r"(?:issued|released|published|available).{0,80}(?:before|prior to).{0,40}(?:call|conference)",
            clause,
        )) or (release_clock and bool(re.search(
            r"\b(?:before|prior to)\s+(?:(?:the\s+)?company[’']s\s+|(?:the|its)\s+)?"
            r"(?:earnings\s+)?(?:conference\s+)?call\b",
            clause,
        ))) or match.start() == bounded_call_clock
        if call_clock and release_linked_to_call and _call_bound_spans_another_day(clause):
            continue
        if mentions_call and not release_linked_to_call and (not release_clock or call_clock):
            continue
        zone = (match.group("zone") or "").lower()
        if not zone:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower().replace(".", "")
        if not 0 <= minute <= 59:
            continue
        if meridiem:
            if not 1 <= hour <= 12:
                continue
            if hour == 12:
                hour = 0
            if meridiem == "pm":
                hour += 12
        elif match.group(2) is None or not 0 <= hour <= 23:
            continue
        offsets = {"est": -5, "edt": -4, "cst": -6, "cdt": -5,
                   "mst": -7, "mdt": -6, "pst": -8, "pdt": -7}
        if "standard" in zone:
            zone = zone[0] + "st"
        elif "daylight" in zone:
            zone = zone[0] + "dt"
        if zone in offsets:
            source_zone = timezone(timedelta(hours=offsets[zone]))
        else:
            region = {"e": "New_York", "c": "Chicago", "m": "Denver", "p": "Los_Angeles"}[zone[0]]
            source_zone = ZoneInfo(f"America/{region}")
        clock = datetime(report_day.year, report_day.month, report_day.day, hour, minute,
                         tzinfo=source_zone).astimezone(ZoneInfo("America/New_York"))
        if clock.date() != report_day:
            continue
        minutes = clock.hour * 60 + clock.minute
        # A one-sided publication bound only proves a session when the entire
        # allowed interval is outside trading hours on the stated release day.
        bound = re.search(
            r"\b(not\s+)?(before|after|prior to|following|by|no later than|no earlier than|until)\s*"
            r"(?:(?:about|approximately|around)\s+)?$",
            prefix,
        )
        if bound:
            upper_bound = bound.group(2) in {"before", "prior to", "by", "no later than", "until"}
            if bound.group(1):
                upper_bound = not upper_bound
            if (upper_bound and minutes >= 9 * 60 + 30) or (not upper_bound and minutes < 16 * 60):
                continue
        # "Before a 5pm call" does not prove after-close release. The bound is
        # sufficient only when the call itself is before the market opens.
        if call_clock and release_linked_to_call and minutes >= 9 * 60 + 30:
            continue
        times.append(minutes)
    sessions = {"bmo" if minutes < 9 * 60 + 30 else "amc"
                for minutes in times if minutes < 9 * 60 + 30 or minutes >= 16 * 60}
    return sessions.pop() if len(sessions) == 1 else "unknown"


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


def _is_announcement_logistics_date(text: str, match: re.Match[str]) -> bool:
    """Exclude call logistics from the candidates for an earnings release."""
    prefix = re.split(r"[.!?;]\s+(?=[A-Z])", text[:match.start()])[-1]
    suffix = re.split(r"[.!?;]\s+(?=[A-Z])", text[match.end():], maxsplit=1)[0]
    # Coordinated clauses can put registration and release dates in the same
    # sentence. Keep the candidate's own clause; commas inside dates stay put.
    clause_boundary = r";\s*|,\s*(?:and|while|but)\s+"
    prefix = re.split(clause_boundary, prefix, flags=re.I)[-1]
    suffix = re.split(clause_boundary, suffix, maxsplit=1, flags=re.I)[0]
    if re.search(r"\b(?:replay|recording|archive)\b[^.!?;]{0,120}"
                 r"\b(?:until|through|expires?(?:\s+on)?)\s*"
                 + WEEKDAY_PREFIX + "$",
                 prefix, re.I):
        return True
    sentence = prefix + match.group() + suffix
    if not re.search(r"\b(?:registration|register|submit|upvote)\b|\bq&a\s+platform\b", sentence, re.I):
        return False
    # Keep an actual results-release date even if its own clause also mentions
    # registration. Do not borrow release language from neighboring clauses,
    # as the ordinary date-scoring window can otherwise do.
    active_release = re.search(
        r"\b(?:will|to)\s+(?:(?:also|publicly|electronically|formally)\s+){0,2}"
        r"(?:release|issue|publish|post|report|announce)\b"
        r"(?P<object>[^.!?;]{0,120}?)\b(?:results|earnings)\b", sentence, re.I,
    )
    passive_release = re.search(
        r"\b(?:results|earnings release)\b(?P<object>[^.!?;]{0,120}?)"
        r"\b(?:will be|(?:is|are) scheduled to be)\s+(?:made\s+)?"
        r"(?:available|released|issued|published|posted|reported|announced)\b", sentence, re.I,
    )
    nonrelease = r"\b(?:call|conference|webcast|replay|discussion|registration|questions)\b"
    return not any(release and not re.search(nonrelease, release.group("object"), re.I)
                   for release in (active_release, passive_release))


def extract_earnings_announcement(
    text: str,
    *,
    published_on: date,
) -> dict[str, str] | None:
    cleaned = _clean_text(text)
    lower = cleaned.lower()
    if not any(term in lower for term in EARNINGS_TERMS):
        return None

    candidates: list[tuple[int, date, int, int]] = []
    for match in DATE_RE.finditer(cleaned):
        if _is_announcement_logistics_date(cleaned, match):
            continue
        # A future fiscal period end is not a future earnings release. These
        # dates often share a sentence with the release date and otherwise win
        # the contextual-score tie merely because they occur earlier.
        if re.search(r"\b(?:quarter|year|period|months?)\s+end(?:ed|ing|s)?\s+(?:on\s+)?"
                     r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?$",
                     cleaned[max(0, match.start() - 80):match.start()], re.I):
            continue
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
        distance_days = (candidate - published_on).days
        if 0 < distance_days <= 180:
            score += 2
        elif distance_days == 0:
            # Company releases commonly begin with a publication dateline before
            # naming the actual future earnings date. Keep same-day events valid,
            # but do not let the dateline win a tie against a future event.
            score -= 2
        candidates.append((score, candidate, start, end))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    score, candidate, start, end = candidates[0]
    if score < 6:
        return None

    # Keep complete sentences for timing: a fixed-length excerpt can retain a
    # session but cut off the different event date that disqualifies it.
    boundaries = list(re.finditer(r"[.!?;]\s+(?=[A-Z])", cleaned[:start]))
    start = boundaries[-1].end() if boundaries else 0
    boundary = re.search(r"[.!?;]\s+(?=[A-Z])", cleaned[end:])
    end = end + boundary.end() if boundary else len(cleaned)
    context = cleaned[start:end]

    # A cross-sentence bound needs the whole article to establish that its call
    # clock is unique; a date excerpt may hide a later conflicting clock.
    timing = _session_from_text(context, report_day=candidate, allow_cross_sentence=False)
    if timing == "unknown" or len(TIME_RE.findall(cleaned)) > len(TIME_RE.findall(context)):
        timing = _session_from_text(cleaned, report_day=candidate)
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
    latest = max(
        rows,
        key=lambda row: (
            str(row.get("published_on") or ""),
            str(row.get("provider") or ""),
        ),
    )
    # Publication dates have day precision. A summary without a session must
    # not hide an explicit session from the same day's announcement for the
    # same event. Never carry a session across a newer revision or date change.
    contemporaneous = [row for row in rows
                       if row.get("date") == latest.get("date")
                       and row.get("published_on") == latest.get("published_on")]
    explicit = [row for row in contemporaneous
                if normalize_timing(row.get("timing")) != "unknown"]
    sessions = {normalize_timing(row.get("timing")) for row in explicit}
    if len(sessions) > 1:
        return {**latest, "timing": "unknown", "timing_conflict": True}
    if explicit:
        return max(explicit, key=lambda row: str(row.get("provider") or ""))
    return latest


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


def _completed_matches_anchor(
    evidence: Vote, anchor: Vote | None, *, as_of: date,
) -> bool:
    """Permit recent corrections, never replace a verified next-period event.

    DoltHub's projected fiscal quarter can be a calendar ordinal (GOOG Q4 for
    October's Q3 release), so it is not authoritative period evidence.
    """
    if not anchor or not anchor.get("date") or anchor.get("has_actuals"):
        return False
    event_day = date.fromisoformat(str(evidence["date"])[:10])
    anchor_day = date.fromisoformat(str(anchor["date"])[:10])
    if not 0 < (as_of - event_day).days <= COMPLETED_LOOKBACK_DAYS:
        return False
    if not as_of <= anchor_day or abs((anchor_day - event_day).days) > EVENT_CORRECTION_DAYS:
        return False
    if str(anchor.get("confidence") or "").lower() in {"confirmed", "manual"}:
        return False
    if anchor.get("fiscal_identity_trusted"):
        return bool(evidence.get("fiscal_year") and evidence.get("fiscal_q")
                    and str(evidence["fiscal_year"]) == str(anchor.get("fiscal_year"))
                    and evidence["fiscal_q"] == anchor.get("fiscal_q"))
    return True


def _announcement_decision(
    announcements: list[Announcement],
    *,
    as_of: date | None = None,
    anchor: Vote | None = None,
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
            or _completed_matches_anchor(row, anchor, as_of=as_of)
        )
    ]
    # A story returned under both share classes is still one item of evidence.
    unique = {}
    for row in valid:
        key = tuple(str(row.get(field) or "") for field in (
            "provider", "date", "published_on", "url", "title", "timing", "official", "direct",
            "supersedes_date",
        ))
        unique[key] = row
    valid = list(unique.values())
    # A wire may repeat the original announcement on multiple feeds. The
    # issuer's subsequent revision supersedes all copies of that exact date.
    superseded: dict[str, str] = {}
    for row in valid:
        old_date = row.get("supersedes_date")
        if old_date and row.get("published_on"):
            superseded[old_date] = max(superseded.get(old_date, ""), row["published_on"])
    retained = []
    for row in valid:
        published = str(row.get("published_on") or "")
        replaced = superseded.get(str(row["date"]), "")
        if published and replaced and (
            published < replaced or (published == replaced and not row.get("supersedes_date"))
        ):
            continue
        retained.append(row)
    valid = retained
    revisions = [row for row in valid if row.get("supersedes_date") and row.get("published_on")]
    if revisions:
        newest_revision = max(row["published_on"] for row in revisions)
        # Multiple copies cannot break a tie between competing explicit
        # revisions. Publication dates lack the time needed to order them.
        if len({row["date"] for row in revisions if row["published_on"] == newest_revision}) > 1:
            return None, None
    official = [row for row in valid if row.get("official")]
    if official:
        newest = max(str(row.get("published_on") or "") for row in official)
        if len({row["date"] for row in official
                if str(row.get("published_on") or "") == newest}) > 1:
            return None, None
        return _latest_announcement(official), "official_announcement"
    direct = [row for row in valid if row.get("direct")]
    if direct:
        counts = Counter(str(row["date"]) for row in direct)
        max_count = max(counts.values())
        top_dates = {d for d, count in counts.items() if count == max_count}
        top_rows = [row for row in direct if str(row["date"]) in top_dates]
        newest = max(str(row.get("published_on") or "") for row in top_rows)
        if len({row["date"] for row in top_rows
                if str(row.get("published_on") or "") == newest}) > 1:
            return None, None
        return _latest_announcement(top_rows), "direct_announcement"
    return None, None


_INTERNAL_CALENDAR_PROVIDERS = {"dolthub", "dolthub_calendar", "baseline", "current"}


def _structured_date_consensus(votes: list[Vote]) -> tuple[str | None, list[str]]:
    """Return a date only when two independent external calendars agree.

    The current/DoltHub row and the prior published baseline are anchors, not
    independent votes. Counting either of them as corroboration can manufacture
    confidence when a vendor simply repeats the same projected date.
    """
    distinct = [
        vote
        for vote in _distinct_votes(votes)
        if vote["provider"] not in _INTERNAL_CALENDAR_PROVIDERS
    ]
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


def choose_canonical_event(
    *,
    current: Vote | None,
    baseline: Vote | None,
    structured_votes: list[Vote],
    announcements: list[Announcement],
    as_of: date | None = None,
    reported_events: list[Vote] | None = None,
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
    anchor = baseline or current
    votes = _distinct_votes([
        vote for vote in structured_votes
        if as_of is None or str(vote.get("date") or "")[:10] >= as_of.isoformat()
        or _completed_matches_anchor(vote, anchor, as_of=as_of)
    ])
    announcement, announcement_reason = _announcement_decision(
        announcements, as_of=as_of, anchor=anchor,
    )
    completed = [row for row in reported_events or []
                 if as_of is not None and _completed_matches_anchor(row, anchor, as_of=as_of)]
    # Conflicting reported dates are not a basis for rewriting another security.
    reported = completed[0] if len({row["date"] for row in completed}) == 1 else None
    if reported is not None:
        sessions = {normalize_timing(row.get("timing")) for row in completed
                    if normalize_timing(row.get("timing")) != "unknown"}
        reported = {**reported, "timing": sessions.pop() if len(sessions) == 1 else "unknown"}
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
    elif reported is not None:
        chosen_date = str(reported["date"])
        reason = "reported_actuals"
        confidence = "reported"
        date_sources = ["reported_actuals"]
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
    if announcement is None and reported is not None:
        timing = reported["timing"]
        if timing != "unknown":
            timing_sources = ["reported_actuals"]

    if announcement is not None and str(announcement["date"])[:10] == chosen_date:
        announcement_timing = normalize_timing(announcement.get("timing"))
        if announcement_timing != "unknown":
            timing = announcement_timing
            timing_sources = [str(announcement.get("provider") or "announcement")]

    if (
        timing == "unknown"
        and not (announcement or {}).get("timing_conflict")
        and baseline_confirmed
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
        "fiscal_year": (announcement or reported or {}).get("fiscal_year"),
        "fiscal_q": (announcement or reported or {}).get("fiscal_q"),
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


def _reported_vote_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Retain evidence that results exist, never another class's EPS/revenue."""
    actuals = {"eps_actual": row.get("epsActual"), "revenue_actual": row.get("revenueActual")}
    if not _has_actuals(actuals):
        return {}
    metadata: dict[str, Any] = {"has_actuals": True}
    year = str(row.get("fiscalYear") or row.get("year") or "")
    quarter = str(row.get("fiscalQuarter") or row.get("quarter") or "").upper().removeprefix("Q")
    if re.fullmatch(r"20\d{2}", year) and quarter in {"1", "2", "3", "4"}:
        metadata.update(fiscal_year=int(year), fiscal_q=f"Q{quarter}")
    return metadata


def fetch_finnhub_calendar(
    start: date,
    end: date,
    key: str | None,
    *,
    max_requests: int = 32,
    request_delay: float = 1.05,
) -> tuple[list[Vote], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    if end < start or max_requests < 1:
        raise ValueError("calendar window and request budget must be positive")
    # The provider silently caps a response at 1,500 rows. Split saturated
    # windows until every leaf is complete; never treat a capped day as complete.
    pending = [(start, end)]
    votes_by_key: dict[tuple[str, str, str], Vote] = {}
    incomplete: list[dict[str, Any]] = []
    requests_made = 0
    http_status = None
    while pending:
        window_start, window_end = pending.pop()
        window = {"from": window_start.isoformat(), "to": window_end.isoformat()}
        if requests_made >= max_requests:
            incomplete.append({**window, "reason": "request_budget_exhausted"})
            continue
        if requests_made:
            time.sleep(request_delay)
        http_status, payload, error = _request_json(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={**window, "token": key},
            secret=key,
            retries=0,
        )
        requests_made += 1
        rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            incomplete.append({**window, "reason": error or "unexpected_response_shape",
                               "http_status": http_status})
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            vote = _vote("finnhub_calendar", row.get("symbol"), row.get("date"), row.get("hour"))
            if vote and vote["symbol"] and window_start <= date.fromisoformat(vote["date"]) <= window_end:
                votes_by_key[(vote["symbol"], vote["date"], vote["timing"])] = {
                    **vote, **_reported_vote_metadata(row),
                }
        if len(rows) >= 1500:
            if window_start == window_end:
                incomplete.append({**window, "reason": "single_day_row_cap"})
            else:
                middle = window_start + (window_end - window_start) // 2
                pending.extend([(middle + timedelta(days=1), window_end), (window_start, middle)])
    votes = list(votes_by_key.values())
    return votes, {
        "status": ("partial" if votes else "error") if incomplete else "ok",
        "http_status": http_status, "rows": len(votes), "requests": requests_made,
        "coverage_complete": not incomplete, "incomplete_windows": incomplete,
    }


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
        params={"apikey": key, "from": start.isoformat(), "to": end.isoformat(),
                "includeReportTimes": "true"},
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
            votes.append({**vote, **_reported_vote_metadata(row)})
    return votes, {"status": "ok", "http_status": status, "rows": len(votes)}


def _rescheduled_release(text: str, published_on: date) -> dict[str, str] | None:
    # The body must move publication itself. A company can move only its call.
    directions: dict[str, set[date]] = {"from": set(), "to": set()}
    direction_prefix = re.compile(r"\s*,?\s*(from|to|for)\s+" + WEEKDAY_PREFIX, re.I)
    for statement in re.split(r"[.!?;]\s+(?=[A-Z])", text):
        release = re.search(
            r"\brescheduled?\s+(?:(?:the|its)\s+)?"
            r"(?:release\s+of\s+(?:(?:the|its)\s+)?"
            r"(?:(?:first|second|third|fourth)[\s-]+quarter\s+|quarterly\s+|"
            r"fiscal\s+|20\d{2}\s+)*(?:financial\s+)?(?:results|earnings)\b|"
            r"(?:earnings|(?:financial\s+)?results)\s+release\b)"
            r"(?!\s+(?:conference|call|webcast|replay|transcript|recording)\b)"
            r"(?:\s+and\s+(?:(?:its|the)\s+)?(?:conference\s+)?call\b)?", statement, re.I,
        )
        if release is None:
            continue
        # Consume only consecutive "from/to/for DATE" phrases immediately
        # following this release. A later clause can schedule another event.
        cursor = release.end()
        clause_dates: dict[str, set[date]] = {"from": set(), "to": set()}
        while direction := direction_prefix.match(statement, cursor):
            match = DATE_RE.match(statement, direction.end())
            if match is None:
                break
            day = _candidate_date(match, published_on)
            if day is None:
                return None
            key = "from" if direction.group(1).lower() == "from" else "to"
            clause_dates[key].add(day)
            cursor = match.end()
        if clause_dates["to"]:
            for key in directions:
                directions[key].update(clause_dates[key])
    if len(directions["to"]) != 1 or len(directions["from"]) > 1:
        return None
    new_date = directions["to"].pop()
    if new_date < published_on:
        return None
    parsed = {"date": new_date.isoformat(), "timing": _session_from_text(text, report_day=new_date)}
    if directions["from"]:
        parsed["supersedes_date"] = directions["from"].pop().isoformat()
    return parsed


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
    normalized_title = f" {_clean_text(title).lower()} "
    if (any(term in normalized_title for term in HOST_TITLE_ACTIONS)
            or " plans " in normalized_title):
        # Hosting a call does not establish when earnings are released. These
        # titles are useful only when the body separately announces results.
        release_object = r"\b(?:financial results|results|earnings release|earnings(?!\s+(?:call|conference|webcast)))\b"
        action = r"\b(?:will|to)\s+(?:(?:also|publicly|electronically|formally)\s+){0,2}(?:release|issue|publish|post|report|announce)\b"
        nonrelease = r"\b(?:call|conference|webcast|replay|discussion|discuss)\b"
        statements = re.split(r"[.!?;]\s+(?=[A-Z])", _clean_text(body))
        release_statement = False
        for statement in statements:
            active = re.search(action + r"(?P<object>[^.!?]{0,120}?)" + release_object, statement, re.I)
            passive = re.search(release_object + r"(?P<object>[^.!?]{0,120}?)"
                                r"\b(?:will be|(?:is|are) scheduled to be)\s+(?:made\s+)?"
                                r"(?:available|released|issued|published|posted|reported|announced)\b",
                                statement, re.I)
            if active and not re.search(nonrelease, active.group("object"), re.I):
                release_statement = True
            if passive and not re.search(nonrelease, statement[:passive.end()], re.I):
                release_statement = True
        if not release_statement:
            return None
    if " reschedules " in normalized_title:
        parsed = _rescheduled_release(_clean_text(body), published_on)
    else:
        parsed = extract_earnings_announcement(f"{title}. {body}", published_on=published_on)
    if parsed is None:
        return None
    fiscal_identity = extract_fiscal_identity(f"{title}. {body}")
    return {
        "provider": provider,
        **parsed,
        "published_on": published_on.isoformat(),
        "official": official,
        "direct": True,
        "title": _clean_text(title)[:180],
        "url": url,
        **fiscal_identity,
    }


_COMPANY_NAME_STOPWORDS = {
    "the",
    "and",
    "of",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "company",
    "co",
    "plc",
    "holdings",
    "holding",
    "group",
    "enterprises",
    "enterprise",
    "technologies",
    "technology",
    "international",
    "limited",
    "ltd",
    "sa",
    "nv",
}


def load_company_names(path: Path = TICKER_NAMES_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(symbol).strip().upper(): str(name).strip()
        for symbol, name in payload.items()
        if str(symbol).strip() and str(name).strip()
    }


def headline_mentions_company(
    headline: str,
    symbol: str,
    company_name: str | None,
) -> bool:
    """Require the target ticker or company identity in an announcement headline."""
    target = symbol.strip().upper()
    # Internal apostrophes are orthographic variants in issuer names (for
    # example Hawai‘i/Hawaii and Carter's/Carters), not word boundaries.
    headline = re.sub(r"(?<=\w)['‘’ʼ](?=\w)", "", _clean_text(headline))
    if not target or not headline:
        return False

    if re.search(rf"(?i)(?:\$|\()\s*{re.escape(target)}\s*\)?\b", headline):
        return True

    name = re.sub(r"(?<=\w)['‘’ʼ](?=\w)", "", _clean_text(company_name or ""))
    if not name:
        return False

    # Preserve short/punctuated issuer names (AT&T, 3M) before token filtering.
    if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", headline, flags=re.I):
        return True
    # These established issuer names differ from the legacy legal-name file.
    # Keep aliases explicit: a generic GE prefix also matches GE HealthCare and
    # GE Vernova, which are different public issuers.
    aliases = {"IBM": ("IBM",), "GE": ("GE Aerospace",)}
    if any(re.match(rf"^{re.escape(alias)}(?![A-Za-z0-9])", headline)
           for alias in aliases.get(target, ())):
        return True

    headline_words = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", headline)
        if token
    }
    company_tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", name)
        if token.lower() not in _COMPANY_NAME_STOPWORDS and len(token) >= 3
    ]
    if not company_tokens:
        return False
    # A shared generic word (American, Capital, Financial, etc.) is not an
    # issuer identity. The first distinctive name token allows familiar short
    # forms such as Meta, while ambiguous first tokens require the full name.
    ambiguous = {"american", "national", "first", "united", "general", "global",
                 "capital", "financial", "bank", "ares", "blackrock"}
    if company_tokens[0] in ambiguous:
        return all(token in headline_words for token in company_tokens)
    return company_tokens[0] in headline_words


def finnhub_article_mentions_company(
    article: dict[str, Any],
    symbol: str,
    company_name: str | None,
) -> bool:
    """Related-ticker tags alone are insufficient evidence for Finnhub news."""
    issuer = re.search(r"\b(?:NASDAQ|NYSE)(?:\s+(?:GS|GM|CM|American))?\s*[:：]\s*([A-Z][A-Z0-9.-]*)",
                       str(article.get("summary") or ""), flags=re.I)
    if issuer and issuer.group(1).upper() != symbol.strip().upper():
        return False
    return headline_mentions_company(
        str(article.get("headline") or article.get("title") or ""),
        symbol,
        company_name,
    )


def fetch_finnhub_announcements(
    symbol: str,
    *,
    today: date,
    key: str | None,
    company_name: str | None = None,
    max_requests: int = 12,
    request_delay: float = 1.05,
) -> tuple[list[Announcement], dict[str, Any]]:
    if not key:
        return [], {"status": "missing_key"}
    if max_requests < 1:
        raise ValueError("max_requests must be positive")
    start = today - timedelta(days=120)
    end = today
    evidence: list[Announcement] = []
    seen: set[str] = set()
    windows: list[dict[str, Any]] = []
    truncated_days: list[str] = []
    complete = False
    error = None
    status = 0
    for attempt in range(max_requests):
        if attempt:
            time.sleep(request_delay)
        status, payload, error = _request_json(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": symbol, "from": start.isoformat(),
                    "to": end.isoformat(), "token": key}, secret=key, retries=0,
        )
        rows = payload if isinstance(payload, list) else []
        windows.append({"from": start.isoformat(), "to": end.isoformat(),
                        "articles": len(rows), "http_status": status})
        if not isinstance(payload, list):
            error = error or "unexpected_response_shape"
            break
        days = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            published = _parse_iso_day(row.get("datetime"))
            if published is None or not start <= published <= end:
                continue
            days.append(published)
            identity = str(row.get("id") or row.get("url") or
                           (row.get("datetime"), row.get("headline")))
            if identity in seen:
                continue
            seen.add(identity)
            if not finnhub_article_mentions_company(row, symbol, company_name):
                continue
            item = _announcement_from_article(
                provider="finnhub_company_news", title=str(row.get("headline") or ""),
                body=str(row.get("summary") or ""), published_on=published,
                official=False, url=row.get("url"),
            )
            if item:
                evidence.append(item)
        # The observed provider cap is soft (244-250 rows). Treat 200+ as
        # potentially truncated, overlap the oldest day, then deduplicate.
        if len(rows) < 200:
            complete = not truncated_days
            break
        if any(row["date"] >= today.isoformat() and row["timing"] in {"bmo", "amc"}
               for row in evidence):
            break
        if not days:
            error = "capped_page_without_valid_dates"
            break
        oldest = min(days)
        if oldest == end:
            truncated_days.append(oldest.isoformat())
            end = oldest - timedelta(days=1)
        else:
            end = oldest
        if end < start:
            complete = not truncated_days
            break
    return evidence, {
        "status": "partial" if error else "ok", "http_status": status,
        "error": error, "articles": len(seen), "evidence": len(evidence),
        "requests": len(windows), "coverage_complete": complete,
        "truncated_days": truncated_days, "windows": windows,
    }


def fetch_twelvedata_announcements(
    symbol: str,
    *,
    today: date,
    key: str | None,
    company_name: str | None = None,
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
        title = str(row.get("title") or "")
        if not headline_mentions_company(title, symbol, company_name):
            continue
        item = _announcement_from_article(
            provider="twelvedata_press_release",
            title=title,
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


def fetch_public_announcements(
    symbol: str, *, today: date, company_name: str | None = None,
) -> tuple[list[Announcement], dict[str, Any]]:
    # Imported lazily so the transport module can reuse the existing strict
    # title, issuer, date and release-session parser without an import cycle.
    from earnings_press_releases import fetch_public_announcements as fetch

    rows, meta = fetch(symbol, today=today, company_name=company_name)
    chosen, _ = _announcement_decision(rows, as_of=today)
    if meta.get("http_status") in {403, 429} or chosen and normalize_timing(chosen.get("timing")) in {"bmo", "amc"}:
        return rows, meta

    from earnings_prnewswire import fetch_prnewswire_announcements

    additional, other = fetch_prnewswire_announcements(symbol, today=today, company_name=company_name)
    return rows + additional, {
        "status": other.get("status", "error") if other.get("status") != "ok" else meta.get("status", "ok"),
        "http_status": other.get("http_status"),
        "requests": meta.get("requests", 0) + other.get("requests", 0),
        "evidence": len(rows) + len(additional),
        "sources": {"financialcontent": meta, "prnewswire": other},
    }


def fetch_alphavantage_announcements(
    symbol: str,
    *,
    today: date,
    key: str | None,
    company_name: str | None = None,
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
        title = str(row.get("title") or "")
        if not alpha_article_mentions_symbol(row, symbol):
            continue
        if not headline_mentions_company(title, symbol, company_name):
            continue
        item = _announcement_from_article(
            provider="alphavantage_news",
            title=title,
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


def _has_actuals(row: pd.Series | dict[str, Any]) -> bool:
    for key in ("eps_actual", "revenue_actual"):
        try:
            value = row.get(key)
            if not isinstance(value, bool) and math.isfinite(float(value)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def load_share_class_groups(path: Path = SHARE_CLASSES_PATH) -> dict[str, tuple[str, ...]]:
    """Only reviewed issuer identity may connect share-class calendars."""
    groups: dict[str, tuple[str, ...]] = {}
    for issuer in json.loads(path.read_text())["issuers"]:
        symbols = tuple(issuer["symbols"])
        if (len(set(symbols)) < 2 or len(set(symbols)) != len(symbols)
                or not issuer.get("cik") or not issuer.get("source_url")):
            raise ValueError("share-class issuer needs distinct symbols and primary-source evidence")
        for symbol in symbols:
            if symbol in groups or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
                raise ValueError(f"invalid or overlapping share-class symbol: {symbol}")
            groups[symbol] = symbols
    return groups


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
                "financialcontent_press_release",
                "prnewswire_press_release",
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
        "has_actuals": _has_actuals(row),
        "fiscal_year": row.get("fiscal_year"),
        "fiscal_q": row.get("fiscal_q"),
        "fiscal_identity_trusted": bool(source_parts.intersection({
            "finnhub", "fmp", "alphavantage", "override",
            "twelvedata_press_release", "finnhub_company_news",
            "financialcontent_press_release", "prnewswire_press_release", "alphavantage_news",
        })),
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
    share_classes = load_share_class_groups()
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
    for symbol in tuple(symbols):
        symbols.update(share_classes.get(symbol, ()))
    if allowed_symbols is not None:
        symbols.intersection_update(allowed_symbols)

    drop_indices: set[int] = set()
    replacement_rows: list[dict[str, Any]] = []
    decisions: dict[str, Any] = {}

    recent_rows = current_df.loc[current_df["date"].map(
        lambda value: start - timedelta(days=COMPLETED_LOOKBACK_DAYS) <= value < start
    )]
    reported_by_symbol: dict[str, list[Vote]] = defaultdict(list)
    for _, row in recent_rows.iterrows():
        if _has_actuals(row):
            reported_by_symbol[row["act_symbol"]].append({
                "date": row["date"].isoformat(), "fiscal_year": row["fiscal_year"],
                "fiscal_q": row["fiscal_q"], "evidence_symbol": row["act_symbol"],
                # Actuals prove a report occurred, not the release session.
                # Reuse sessions only from already reconciled/verified history.
                "timing": (normalize_timing(row["timing"])
                           if "reconciled" in str(row["source"]).split("+")
                           or _row_vote(row, "history")["confidence"] == "confirmed"
                           else "unknown"),
            })

    for symbol in sorted(symbols):
        peers = share_classes.get(symbol, (symbol,))
        announcements = [{**row, "evidence_symbol": peer}
                         for peer in peers for row in announcements_by_symbol.get(peer, [])]
        reported_events = [row for peer in peers for row in reported_by_symbol.get(peer, [])]
        reported_events.extend({**vote, "timing": "unknown", "evidence_symbol": peer}
                               for peer in peers for vote in provider_map.get(peer, [])
                               if vote.get("has_actuals"))
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
        for peer in peers:
            for vote in provider_map.get(peer, []):
                by_provider[str(vote["provider"])].append({**vote, "evidence_symbol": peer})
        for provider_votes in by_provider.values():
            # Select one event per share class, then require that this provider
            # agrees across classes. Two share classes are never two providers.
            selected_by_peer = [_closest_vote(
                [vote for vote in provider_votes if vote["evidence_symbol"] == peer], anchor=anchor,
            ) for peer in peers]
            selected_by_peer = [vote for vote in selected_by_peer if vote is not None]
            if len({vote["date"] for vote in selected_by_peer}) == 1:
                sessions = {normalize_timing(vote.get("timing")) for vote in selected_by_peer
                            if normalize_timing(vote.get("timing")) != "unknown"}
                votes.append({**selected_by_peer[0],
                              "timing": sessions.pop() if len(sessions) == 1 else "unknown"})

        decision = choose_canonical_event(
            current=current_vote,
            baseline=baseline_vote,
            structured_votes=votes,
            announcements=announcements,
            as_of=start,
            reported_events=reported_events,
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
                    "supersedes_date",
                    "official",
                    "direct",
                    "title",
                    "url",
                    "fiscal_year",
                    "fiscal_q",
                    "evidence_symbol",
                )
            }
            for row in announcements
        ]
        decision["reported_evidence"] = reported_events
        decisions[symbol] = decision

        chosen_date_text = decision.get("date")
        if not chosen_date_text:
            continue
        chosen_date = date.fromisoformat(str(chosen_date_text))

        # When a phantom future row collapses into a recorded event, retain
        # that ticker's own actuals. Never copy another class's financial data.
        recorded_rows = current_df.loc[
            current_df["act_symbol"].eq(symbol) & current_df["date"].eq(chosen_date)
        ]
        recorded_row = _pick_anchor_row(recorded_rows)
        if recorded_row is not None:
            replacement = recorded_row.to_dict()
        elif current_row is not None:
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
        if (recorded_row is None or not _has_actuals(recorded_row)
                or decision["timing"] != "unknown"):
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
            if not _has_actuals(row) and abs((row["date"] - chosen_date).days) <= EVENT_CORRECTION_DAYS:
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
    preliminary_decisions: dict[str, dict[str, Any]] | None = None,
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

    def external_dates(decision: dict[str, Any]) -> set[str]:
        return {
            str(vote.get("date") or "")[:10]
            for vote in decision.get("structured_votes") or []
            if str(vote.get("provider") or "") not in _INTERNAL_CALENDAR_PROVIDERS
            and vote.get("date")
        }

    def needs_lookup(symbol: str) -> bool:
        if preliminary_decisions is None:
            return True
        decision = preliminary_decisions.get(symbol) or {}
        reason = str(decision.get("reason") or "")
        chosen = str(decision.get("date") or "")[:10]
        conflicts = bool(
            chosen and any(candidate != chosen for candidate in external_dates(decision))
        )
        if reason in {"baseline_confirmed", "official_announcement", "direct_announcement"}:
            return conflicts or normalize_timing(decision.get("timing")) == "unknown"
        return reason not in {"no_candidate", "official_announcement", "direct_announcement"}

    def risk_rank(symbol: str) -> tuple[int, int, str]:
        decision = (preliminary_decisions or {}).get(symbol) or {}
        reason = str(decision.get("reason") or "")
        chosen = str(decision.get("date") or "")[:10]
        timing = normalize_timing(decision.get("timing"))
        external = external_dates(decision)
        conflicts = bool(chosen and any(candidate != chosen for candidate in external))

        if reason == "uncorroborated_new_event":
            rank = 0
        elif reason == "structured_consensus":
            rank = 1
        elif conflicts:
            rank = 2
        elif timing == "unknown":
            rank = 3
        elif reason in {"baseline_sticky", "current_base"}:
            rank = 4
        else:
            rank = 5
        return rank, abs((nearest[symbol] - start).days), symbol

    candidates = [symbol for symbol in nearest if needs_lookup(symbol)]
    ordered = (
        sorted(candidates, key=risk_rank)
        if preliminary_decisions is not None
        else sorted(
            candidates,
            key=lambda symbol: (abs((nearest[symbol] - start).days), symbol),
        )
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
    twelvedata_max_symbols: int = 80,
    finnhub_max_requests: int = 3000,
    company_names: dict[str, str] | None = None,
    candidate_dates: dict[str, str] | None = None,
    public_max_symbols: int = 0,
    public_delay: float = 1.0,
    lookup_state: dict[str, Any] | None = None,
) -> tuple[dict[str, list[Announcement]], dict[str, Any]]:
    evidence: dict[str, list[Announcement]] = defaultdict(list)
    status: dict[str, Any] = {
        "finnhub_company_news": {},
        "twelvedata_press_releases": {},
        "alphavantage_news": {},
        "public_press_releases": {},
    }

    if finnhub_max_requests < len(symbols):
        raise ValueError("news budget must cover one request per candidate")
    remaining = finnhub_max_requests
    for index, symbol in enumerate(symbols):
        # Every candidate gets a first page before extra work may consume its
        # share. High-volume names cannot starve later reporting dates.
        budget = min(12, remaining - (len(symbols) - index - 1))
        rows, meta = fetch_finnhub_announcements(
            symbol,
            today=today,
            key=finnhub_key,
            company_name=(company_names or {}).get(symbol),
            max_requests=budget,
            request_delay=finnhub_delay,
        )
        remaining -= meta.get("requests", 0)
        evidence[symbol].extend(rows)
        status["finnhub_company_news"][symbol] = meta
        if index + 1 < len(symbols) and finnhub_key:
            time.sleep(finnhub_delay)

    def unresolved_symbols() -> list[str]:
        pending = []
        for index, symbol in enumerate(symbols):
            expected_date = (candidate_dates or {}).get(symbol)
            chosen, _ = _announcement_decision(
                evidence[symbol], as_of=today,
                anchor={"date": expected_date} if expected_date else None,
            )
            same_event = not expected_date or chosen and chosen["date"] == expected_date
            if chosen and same_event and normalize_timing(chosen.get("timing")) in {"bmo", "amc"}:
                continue
            # Full press releases can fill a truncated summary's missing
            # session. Prefer the nearest announced event, then retain the
            # original calendar risk order for names without direct evidence.
            announced_date = (str(chosen["date"]) if chosen
                              else expected_date or date.max.isoformat())
            pending.append((announced_date, index, symbol))
        return [symbol for _, _, symbol in sorted(pending)]

    def prioritized(provider: str) -> list[str]:
        return announcement_state.prioritize_symbols(
            unresolved_symbols(), state=lookup_state or {}, provider=provider,
            candidate_dates=candidate_dates or {},
        )

    public_symbols = prioritized("public_press_releases")[:public_max_symbols]
    for index, symbol in enumerate(public_symbols):
        rows, meta = fetch_public_announcements(
            symbol, today=today, company_name=(company_names or {}).get(symbol),
        )
        evidence[symbol].extend(rows)
        status["public_press_releases"][symbol] = meta
        if meta.get("http_status") in {403, 429}:
            print(f"public announcement lookups stopped: HTTP {meta['http_status']}", flush=True)
            break
        if (index + 1) % 50 == 0 or index + 1 == len(public_symbols):
            print(f"public announcement lookups={index + 1}/{len(public_symbols)}; "
                  f"unresolved={len(unresolved_symbols())}", flush=True)
        if index + 1 < len(public_symbols):
            time.sleep(public_delay)

    press_symbols = prioritized("twelvedata_press_releases")[:twelvedata_max_symbols]
    for index, symbol in enumerate(press_symbols):
        rows, meta = fetch_twelvedata_announcements(
            symbol,
            today=today,
            key=twelvedata_key,
            company_name=(company_names or {}).get(symbol),
        )
        evidence[symbol].extend(rows)
        status["twelvedata_press_releases"][symbol] = meta
        if index + 1 < len(press_symbols) and twelvedata_key:
            time.sleep(twelvedata_delay)

    # Reassess after press releases so resolved names do not consume another
    # provider's budget.
    alpha_symbols = unresolved_symbols()[:alpha_news_max]
    for index, symbol in enumerate(alpha_symbols):
        rows, meta = fetch_alphavantage_announcements(
            symbol,
            today=today,
            key=alphavantage_key,
            company_name=(company_names or {}).get(symbol),
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
    parser.add_argument("--announcement-max-symbols", type=int, default=2000)
    parser.add_argument("--twelvedata-max-symbols", type=int, default=80)
    parser.add_argument("--public-max-symbols", type=int, default=2000)
    parser.add_argument("--public-delay", type=float, default=1.0)
    parser.add_argument("--symbols", nargs="+", help="Limit announcement lookups to these tickers")
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
    if args.twelvedata_max_symbols < 0:
        parser.error("--twelvedata-max-symbols cannot be negative")
    if args.public_max_symbols < 0 or args.public_delay < 0:
        parser.error("public lookup limits and delay cannot be negative")

    if args.symbols and any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol) for symbol in args.symbols):
        parser.error("--symbols requires uppercase ticker symbols")

    today = et_today()
    end = today + timedelta(days=args.days_ahead)
    announcement_end = today + timedelta(days=args.announcement_days_ahead)
    data_dir = args.data_dir or default_data_dir()
    state_path = data_dir / "calendar_announcement_state.json"
    lookup_state = announcement_state.load_state(state_path, today=today)

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
    # A source that has already advanced must still be able to correct a
    # nearby stale future projection. Past-only votes never create new rows.
    evidence_start = today - timedelta(days=COMPLETED_LOOKBACK_DAYS)

    finnhub_votes, provider_status["finnhub_calendar"] = fetch_finnhub_calendar(
        evidence_start,
        end,
        finnhub_key,
    )
    provider_votes.extend(finnhub_votes)

    alpha_votes, provider_status["alphavantage_calendar"] = fetch_alphavantage_calendar(
        evidence_start,
        end,
        alphavantage_key,
    )
    provider_votes.extend(alpha_votes)

    fmp_votes, provider_status["fmp_calendar"] = fetch_fmp_calendar(
        evidence_start,
        end,
        fmp_key,
    )
    provider_votes.extend(fmp_votes)

    frontend_symbols = set(load_frontend_symbol_universe())
    data_symbols, fresh_symbols = load_data_backed_symbol_universe(
        data_dir, today=today, now=datetime.now(timezone.utc),
    )
    allowed_symbols = frontend_symbols | data_symbols

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
        allowed_symbols=frontend_symbols | fresh_symbols,
        preliminary_decisions=preliminary_report["decisions"],
    )
    candidate_dates = {symbol: decision["date"]
                       for symbol, decision in preliminary_report["decisions"].items()
                       if decision.get("date")}
    if args.symbols:
        requested = set(args.symbols)
        symbols = [symbol for symbol in symbols if symbol in requested]
    else:
        # A growing price-backed universe must not starve existing calendar
        # repairs or add thousands of daily provider calls. Unsearched newcomers
        # still pass the full safety reconciliation and cannot retain raw timing.
        published = [symbol for symbol in symbols if symbol in frontend_symbols]
        newcomers = [symbol for symbol in symbols if symbol not in frontend_symbols]
        newcomers = announcement_state.prioritize_symbols(
            newcomers, state=lookup_state, provider="public_press_releases",
            candidate_dates=candidate_dates,
        )
        symbols = published + newcomers[:MAX_NEW_ANNOUNCEMENT_SYMBOLS]
    share_classes = load_share_class_groups()
    for symbol in tuple(symbols):
        for peer in share_classes.get(symbol, ()):
            if peer in allowed_symbols and peer not in symbols:
                symbols.append(peer)
    symbols = symbols[:args.announcement_max_symbols]
    company_names = load_company_names()
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
        twelvedata_max_symbols=args.twelvedata_max_symbols,
        company_names=company_names,
        candidate_dates=candidate_dates,
        public_max_symbols=args.public_max_symbols,
        public_delay=args.public_delay,
        lookup_state=lookup_state,
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
            "evidence_start": evidence_start.isoformat(),
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
    observed_at = datetime.now(timezone.utc).isoformat()
    for provider in ("public_press_releases", "twelvedata_press_releases"):
        for symbol, meta in announcement_status[provider].items():
            if not candidate_dates.get(symbol) or meta.get("status") == "missing_key":
                continue
            lookup_state = announcement_state.update_state(
                lookup_state, provider=provider, symbol=symbol,
                event_date=candidate_dates[symbol], observed_at=observed_at,
                status=meta.get("status", "unknown"),
            )
    announcement_state.write_state(state_path, lookup_state, today=today)
    print(f"wrote {data_dir / 'earnings_calendar.csv'}")
    print(f"wrote {data_dir / 'earnings_calendar.parquet'}")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
