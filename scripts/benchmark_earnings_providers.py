#!/usr/bin/env python3
"""Read-only benchmark of earnings-calendar providers against IR-verified events.

This script never mutates Quantiv's production earnings calendar. It calls
configured provider APIs, normalizes only date/session/confirmation metadata,
and writes sanitized benchmark artifacts.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import requests


DEFAULT_TRUTH: dict[str, dict[str, str] | None] = {
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

Record = dict[str, str]
ProviderResult = dict[str, Any]


def normalize_session(value: Any) -> str:
    if value is None:
        return "unknown"
    raw = str(value).strip()
    if not raw:
        return "unknown"
    s = re.sub(r"[\s_-]+", " ", raw.lower()).strip()
    if s in {
        "bmo",
        "pre market",
        "premarket",
        "before open",
        "before market open",
        "before market",
    }:
        return "bmo"
    if s in {
        "amc",
        "after hours",
        "after market",
        "after close",
        "after market close",
        "post market",
        "postmarket",
    }:
        return "amc"
    if s in {"time not supplied", "unknown", "n/a", "na", "none"}:
        return "unknown"

    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(am|pm))?", s)
    if not match:
        return "unknown"
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(4)
    if ampm:
        if hour == 12:
            hour = 0
        if ampm == "pm":
            hour += 12
    minutes = hour * 60 + minute
    if minutes < 9 * 60 + 30:
        return "bmo"
    if minutes >= 16 * 60:
        return "amc"
    return "unknown"


def normalize_confirmation(value: Any) -> str:
    if isinstance(value, bool):
        return "confirmed" if value else "projected"
    s = str(value or "").strip().lower()
    if "confirm" in s:
        return "confirmed"
    if "project" in s or "estimate" in s or "unconfirm" in s:
        return "projected"
    return "unknown"


def _record(
    ticker: Any,
    event_date: Any,
    session: Any = None,
    confirmation: Any = None,
) -> Record | None:
    symbol = str(ticker or "").strip().upper()
    d = str(event_date or "").strip()[:10]
    if not symbol or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return None
    try:
        date.fromisoformat(d)
    except ValueError:
        return None
    return {
        "ticker": symbol,
        "date": d,
        "session": normalize_session(session),
        "confirmation": normalize_confirmation(confirmation),
    }


def parse_finnhub(payload: Any) -> list[Record]:
    if not isinstance(payload, dict):
        return []
    records = []
    for row in payload.get("earningsCalendar") or []:
        if not isinstance(row, dict):
            continue
        rec = _record(row.get("symbol"), row.get("date"), row.get("hour"))
        if rec:
            records.append(rec)
    return records


def parse_twelvedata(payload: Any) -> list[Record]:
    if not isinstance(payload, dict):
        return []
    earnings = payload.get("earnings") or {}
    records: list[Record] = []
    if isinstance(earnings, dict):
        for event_date, rows in earnings.items():
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                rec = _record(row.get("symbol"), event_date, row.get("time"))
                if rec:
                    records.append(rec)
    elif isinstance(earnings, list):
        for row in earnings:
            if not isinstance(row, dict):
                continue
            rec = _record(row.get("symbol"), row.get("date"), row.get("time"))
            if rec:
                records.append(rec)
    return records


def parse_fmp(payload: Any) -> list[Record]:
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("results") or []
    records: list[Record] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        session = next(
            (
                row.get(key)
                for key in (
                    "time",
                    "reportTime",
                    "reportingTime",
                    "estimatedReportTime",
                    "report_time",
                )
                if row.get(key) not in (None, "")
            ),
            None,
        )
        confirmation = next(
            (
                row.get(key)
                for key in (
                    "dateStatus",
                    "date_status",
                    "confirmationStatus",
                    "confirmed",
                    "isConfirmed",
                )
                if row.get(key) not in (None, "")
            ),
            None,
        )
        rec = _record(row.get("symbol"), row.get("date"), session, confirmation)
        if rec:
            records.append(rec)
    return records


def parse_massive(payload: Any) -> list[Record]:
    if not isinstance(payload, dict):
        return []
    records: list[Record] = []
    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue
        rec = _record(
            row.get("ticker"),
            row.get("date"),
            row.get("time"),
            row.get("date_status"),
        )
        if rec:
            records.append(rec)
    return records


def parse_alphavantage_csv(text: str) -> list[Record]:
    if not text or text.lstrip().startswith("{"):
        return []
    records: list[Record] = []
    for row in csv.DictReader(io.StringIO(text)):
        rec = _record(
            row.get("symbol"),
            row.get("reportDate") or row.get("date"),
        )
        if rec:
            records.append(rec)
    return records


def _records_by_ticker(records: list[Record]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for record in records:
        key = (
            record["ticker"],
            record["date"],
            record["session"],
            record["confirmation"],
        )
        if key in seen:
            continue
        seen.add(key)
        grouped[record["ticker"]].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["date"], row["session"]))
    return dict(grouped)


def score_provider(
    records: list[Record],
    truth: dict[str, dict[str, str] | None],
) -> dict[str, Any]:
    grouped = _records_by_ticker(records)
    truth_events = sum(expected is not None for expected in truth.values())
    date_hits = 0
    session_hits = 0
    session_known_truth = 0
    wrong_dates: dict[str, list[str]] = {}
    wrong_sessions: dict[str, list[str]] = {}
    false_positives: dict[str, list[str]] = {}
    missing: list[str] = []
    session_missing: list[str] = []

    for ticker, expected in truth.items():
        rows = grouped.get(ticker, [])
        if expected is None:
            if rows:
                false_positives[ticker] = sorted({row["date"] for row in rows})
            continue

        expected_date = expected["date"]
        expected_session = expected.get("session", "unknown")
        if expected_session != "unknown":
            session_known_truth += 1

        exact = [row for row in rows if row["date"] == expected_date]
        if not exact:
            if rows:
                wrong_dates[ticker] = sorted({row["date"] for row in rows})
            else:
                missing.append(ticker)
            continue

        date_hits += 1
        if expected_session == "unknown":
            continue
        sessions = sorted({row["session"] for row in exact})
        if expected_session in sessions:
            session_hits += 1
        elif any(session != "unknown" for session in sessions):
            wrong_sessions[ticker] = sessions
        else:
            session_missing.append(ticker)

    return {
        "truth_events": truth_events,
        "date_hits": date_hits,
        "date_accuracy": date_hits / truth_events if truth_events else None,
        "session_known_truth": session_known_truth,
        "session_hits": session_hits,
        "session_accuracy": (
            session_hits / session_known_truth if session_known_truth else None
        ),
        "wrong_dates": wrong_dates,
        "wrong_sessions": wrong_sessions,
        "false_positives": false_positives,
        "missing": sorted(missing),
        "session_missing": sorted(session_missing),
    }


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _safe_error(response: requests.Response, secret: str) -> str:
    text = (response.text or "").strip().replace(secret, "[redacted]")
    text = re.sub(r"(?i)(api[_-]?key|token)=[^&\s]+", r"\1=[redacted]", text)
    return text[:300]


def _request_json(
    url: str,
    *,
    params: dict[str, Any],
    secret: str,
    timeout: float = 45,
) -> tuple[int, Any, str | None]:
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        return 0, None, type(exc).__name__
    if not response.ok:
        return response.status_code, None, _safe_error(response, secret)
    try:
        return response.status_code, response.json(), None
    except ValueError:
        return response.status_code, None, "invalid_json"


def _request_text(
    url: str,
    *,
    params: dict[str, Any],
    secret: str,
    timeout: float = 45,
) -> tuple[int, str | None, str | None]:
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        return 0, None, type(exc).__name__
    if not response.ok:
        return response.status_code, None, _safe_error(response, secret)
    return response.status_code, response.text, None


def fetch_finnhub(start: str, end: str) -> ProviderResult:
    key = _first_env("FINNHUB_API_KEY")
    if not key:
        return {"status": "missing_key", "records": [], "http_status": None}
    status, payload, error = _request_json(
        "https://finnhub.io/api/v1/calendar/earnings",
        params={"from": start, "to": end, "token": key},
        secret=key,
    )
    records = parse_finnhub(payload)
    return {
        "status": "ok" if payload is not None else "error",
        "http_status": status,
        "error": error,
        "records": records,
        "raw_row_count": len((payload or {}).get("earningsCalendar") or [])
        if isinstance(payload, dict)
        else 0,
    }


def fetch_twelvedata(start: str, end: str) -> ProviderResult:
    key = _first_env("TWELVEDATA_API_KEY", "TWELVEDATA_API_KEY_2")
    if not key:
        return {"status": "missing_key", "records": [], "http_status": None}
    status, payload, error = _request_json(
        "https://api.twelvedata.com/earnings_calendar",
        params={
            "start_date": start,
            "end_date": end,
            "country": "United States",
            "format": "JSON",
            "apikey": key,
        },
        secret=key,
    )
    records = parse_twelvedata(payload)
    provider_error = (
        str(payload.get("message"))[:300]
        if isinstance(payload, dict) and payload.get("status") == "error"
        else None
    )
    raw_count = 0
    if isinstance(payload, dict) and isinstance(payload.get("earnings"), dict):
        raw_count = sum(len(rows or []) for rows in payload["earnings"].values())
    return {
        "status": (
            "ok"
            if payload is not None
            and not (isinstance(payload, dict) and payload.get("status") == "error")
            else "error"
        ),
        "http_status": status,
        "error": error or provider_error,
        "records": records,
        "raw_row_count": raw_count,
    }


def fetch_fmp(start: str, end: str) -> ProviderResult:
    key = _first_env("FMP_API_KEY", "FMP_API_KEY_2")
    if not key:
        return {"status": "missing_key", "records": [], "http_status": None}

    attempts = [
        {
            "from": start,
            "to": end,
            "includeReportTimes": "true",
            "apikey": key,
        },
        {"includeReportTimes": "true", "page": 0, "apikey": key},
        {"page": 0, "apikey": key},
    ]
    attempt_results: list[dict[str, Any]] = []
    for index, params in enumerate(attempts, start=1):
        status, payload, error = _request_json(
            "https://financialmodelingprep.com/stable/earnings-calendar",
            params=params,
            secret=key,
        )
        attempt_results.append(
            {"attempt": index, "http_status": status, "error": error}
        )
        if payload is None:
            continue
        records = parse_fmp(payload)
        raw_rows = payload if isinstance(payload, list) else []
        return {
            "status": "ok",
            "http_status": status,
            "error": None,
            "records": records,
            "raw_row_count": len(raw_rows),
            "attempts": attempt_results,
            "observed_fields": sorted(
                {
                    field
                    for row in raw_rows
                    if isinstance(row, dict)
                    for field in row
                }
            ),
        }
    return {
        "status": "error",
        "http_status": attempt_results[-1]["http_status"],
        "error": attempt_results[-1]["error"],
        "records": [],
        "attempts": attempt_results,
    }


def fetch_alphavantage(start: str, end: str) -> ProviderResult:
    del start, end
    key = _first_env(
        "ALPHAVANTAGE_API_KEY",
        "ALPHAVANTAGE_API_KEY_2",
        "ALPHAVANTAGE_API_KEY_3",
    )
    if not key:
        return {"status": "missing_key", "records": [], "http_status": None}
    status, text, error = _request_text(
        "https://www.alphavantage.co/query",
        params={"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": key},
        secret=key,
    )
    if text is None:
        return {
            "status": "error",
            "http_status": status,
            "error": error,
            "records": [],
        }
    if text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        msg = (
            payload.get("Information")
            or payload.get("Note")
            or payload.get("Error Message")
        )
        return {
            "status": "error",
            "http_status": status,
            "error": str(msg or "json_error_response")[:300],
            "records": [],
        }
    records = parse_alphavantage_csv(text)
    return {
        "status": "ok",
        "http_status": status,
        "error": None,
        "records": records,
        "raw_row_count": max(0, len(text.splitlines()) - 1),
    }


def fetch_massive(start: str, end: str) -> ProviderResult:
    key = _first_env("POLYGON_API_KEY", "POLYGON_API_KEY_2", "MASSIVE_API_KEY")
    if not key:
        return {"status": "missing_key", "records": [], "http_status": None}
    status, payload, error = _request_json(
        "https://api.massive.com/benzinga/v1/earnings",
        params={
            "date.gte": start,
            "date.lte": end,
            "limit": 5000,
            "sort": "date.asc",
            "apiKey": key,
        },
        secret=key,
    )
    records = parse_massive(payload)
    return {
        "status": "ok" if payload is not None else "error",
        "http_status": status,
        "error": error,
        "records": records,
        "raw_row_count": len((payload or {}).get("results") or [])
        if isinstance(payload, dict)
        else 0,
    }


FETCHERS: dict[str, Callable[[str, str], ProviderResult]] = {
    "finnhub": fetch_finnhub,
    "twelvedata": fetch_twelvedata,
    "fmp": fetch_fmp,
    "alphavantage": fetch_alphavantage,
    "massive_benzinga": fetch_massive,
}


def _window_filter(records: list[Record], start: str, end: str) -> list[Record]:
    return [record for record in records if start <= record["date"] <= end]


def _truth_filter(records: list[Record], truth: dict[str, Any]) -> list[Record]:
    tickers = set(truth)
    return [record for record in records if record["ticker"] in tickers]


def _summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Earnings provider benchmark",
        "",
        f"Window: {report['window']['start']} through {report['window']['end']}",
        f"Truth tickers: {len(report['truth'])}",
        "",
        "| Provider | Status | HTTP | Date accuracy | Session accuracy | False positives | Missing |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, provider in report["providers"].items():
        score = provider.get("score") or {}
        date_acc = score.get("date_accuracy")
        session_acc = score.get("session_accuracy")
        date_text = f"{date_acc:.1%}" if isinstance(date_acc, float) else "n/a"
        session_text = (
            f"{session_acc:.1%}" if isinstance(session_acc, float) else "n/a"
        )
        lines.append(
            f"| {name} | {provider['status']} | {provider.get('http_status') or '-'} "
            f"| {date_text} | {session_text} "
            f"| {len(score.get('false_positives', {}))} "
            f"| {len(score.get('missing', []))} |"
        )

    lines.extend(["", "## Provider details", ""])
    for name, provider in report["providers"].items():
        score = provider.get("score") or {}
        lines.append(f"### {name}")
        lines.append(
            f"- status={provider['status']}, http={provider.get('http_status')}, "
            f"raw_rows={provider.get('raw_row_count', 0)}, "
            f"challenge_rows={len(provider.get('records', []))}"
        )
        if provider.get("error"):
            lines.append(f"- error: {provider['error']}")
        if score:
            lines.append(
                f"- date: {score['date_hits']}/{score['truth_events']}; "
                f"session: {score['session_hits']}/{score['session_known_truth']}"
            )
            for label, key in (
                ("wrong dates", "wrong_dates"),
                ("wrong sessions", "wrong_sessions"),
                ("false positives", "false_positives"),
            ):
                if score.get(key):
                    lines.append(
                        f"- {label}: {json.dumps(score[key], sort_keys=True)}"
                    )
            if score.get("missing"):
                lines.append(f"- missing: {', '.join(score['missing'])}")
            if score.get("session_missing"):
                lines.append(
                    f"- session missing: {', '.join(score['session_missing'])}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_benchmark(start: str, end: str) -> dict[str, Any]:
    truth = DEFAULT_TRUTH
    providers: dict[str, Any] = {}
    for name, fetcher in FETCHERS.items():
        result = fetcher(start, end)
        records = _window_filter(result.pop("records", []), start, end)
        challenge_records = _truth_filter(records, truth)
        result["records"] = challenge_records
        result["score"] = score_provider(challenge_records, truth)
        providers[name] = result

    return {
        "schema": "quantiv.earnings-provider-benchmark.v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "window": {"start": start, "end": end},
        "truth": truth,
        "providers": providers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-09-21")
    parser.add_argument("--end", default="2026-10-06")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/validation/earnings_provider_benchmark"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        date.fromisoformat(args.start)
        date.fromisoformat(args.end)
    except ValueError as exc:
        raise SystemExit(f"invalid benchmark date: {exc}") from exc
    if args.end < args.start:
        raise SystemExit("--end must not precede --start")

    report = run_benchmark(args.start, args.end)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "benchmark.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    summary = _summary_markdown(report)
    (args.output_dir / "summary.md").write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
