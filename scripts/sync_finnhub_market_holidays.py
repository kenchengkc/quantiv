#!/usr/bin/env python3
"""Validate Finnhub's US holiday feed against Quantiv's canonical NYSE sessions.

The production source of truth is ``config/market_sessions.json``. Finnhub is a
secondary provider check only: a vendor response may surface drift, but it never
silently rewrites the exchange calendar consumed by serving code.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import requests

from provider_market_hours import (
    block_finnhub_reserved_window,
    is_finnhub_reserved_window,
)
from sync_finnhub_earnings import load_local_env


REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = REPO_ROOT / "config" / "market_sessions.json"
DIAGNOSTIC_PATH = REPO_ROOT / "data" / "provider_enrichments" / "finnhub_market_sessions.json"
BASE_URL = "https://finnhub.io/api/v1/stock/market-holiday"
SCHEMA = "quantiv.market-sessions.v1"


def fetch_holidays(token: str, exchange: str) -> dict[str, Any]:
    resp = requests.get(
        BASE_URL,
        params={"exchange": exchange, "token": token},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Finnhub market-holiday HTTP {resp.status_code}: {resp.text[:300]}"
        )
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise RuntimeError(
            f"Unexpected Finnhub market-holiday response: {str(body)[:300]}"
        )
    return body


def _normalize_close(value: str) -> str:
    """Normalize Finnhub ``09:30-13:00`` style hours to the close clock."""
    value = value.strip()
    if not value:
        return value
    return value.rsplit("-", 1)[-1].strip()


def split_holidays(rows: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    closed: set[str] = set()
    early_closes: dict[str, str] = {}
    for row in rows:
        at_date = str(row.get("atDate") or row.get("date") or "").strip()
        if len(at_date) < 10:
            continue
        iso = at_date[:10]
        trading_hour = str(row.get("tradingHour") or "").strip()
        if trading_hour:
            early_closes[iso] = _normalize_close(trading_hour)
        else:
            closed.add(iso)
    return sorted(closed), dict(sorted(early_closes.items()))


def load_canonical() -> dict[str, Any]:
    payload = json.loads(SESSION_PATH.read_text())
    if payload.get("schema") != SCHEMA:
        raise RuntimeError(f"unsupported canonical market-session schema: {payload.get('schema')}")
    holidays = payload.get("holidays")
    early = payload.get("early_closes")
    if not isinstance(holidays, list) or not holidays:
        raise RuntimeError("canonical market-session contract has no holidays")
    if not isinstance(early, dict):
        raise RuntimeError("canonical market-session contract has invalid early_closes")
    return payload


def validate_provider_overlap(
    canonical: dict[str, Any], provider_closed: list[str], provider_early: dict[str, str]
) -> list[str]:
    provider_dates = sorted(set(provider_closed) | set(provider_early))
    if not provider_dates:
        return ["provider returned no dated market sessions"]
    start, end = provider_dates[0], provider_dates[-1]
    canonical_closed = {
        day for day in canonical["holidays"] if start <= str(day) <= end
    }
    canonical_early = {
        str(day): str(close)
        for day, close in canonical["early_closes"].items()
        if start <= str(day) <= end
    }
    provider_closed_set = set(provider_closed)
    errors: list[str] = []
    missing_closed = sorted(canonical_closed - provider_closed_set)
    unexpected_closed = sorted(provider_closed_set - canonical_closed)
    if missing_closed:
        errors.append(f"provider missing canonical closures: {missing_closed[:10]}")
    if unexpected_closed:
        errors.append(f"provider has unknown closures: {unexpected_closed[:10]}")
    for day in sorted(set(canonical_early) | set(provider_early)):
        expected = canonical_early.get(day)
        actual = provider_early.get(day)
        if expected != actual:
            errors.append(
                f"early-close mismatch {day}: canonical={expected!r} provider={actual!r}"
            )
    return errors


def write_diagnostic(
    body: dict[str, Any], closed: list[str], early_closes: dict[str, str], errors: list[str]
) -> None:
    DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "quantiv.provider-market-sessions.v1",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "provider": "finnhub /stock/market-holiday",
        "exchange": body.get("exchange") or "US",
        "timezone": body.get("timezone") or "America/New_York",
        "holidays": closed,
        "early_closes": early_closes,
        "canonical_match": not errors,
        "errors": errors,
    }
    DIAGNOSTIC_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", default="US")
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 instead of failing when FINNHUB_API_KEY is missing.",
    )
    parser.add_argument(
        "--allow-market-hours",
        action="store_true",
        help="Allow this non-price provider check during the quote refresh window.",
    )
    parser.add_argument(
        "--skip-during-market-hours",
        action="store_true",
        help="Exit 0 instead of consuming Finnhub quote-window budget.",
    )
    args = parser.parse_args()

    canonical = load_canonical()

    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        msg = "FINNHUB_API_KEY missing"
        if args.allow_missing_key:
            print(f"{msg}; canonical NYSE session contract remains active")
            return 0
        print(msg, file=sys.stderr)
        return 1

    if args.skip_during_market_hours and is_finnhub_reserved_window():
        print("In quote-refresh window; skipping Finnhub calendar validation")
        return 0
    block_finnhub_reserved_window(args.allow_market_hours)

    body = fetch_holidays(token, args.exchange)
    closed, early_closes = split_holidays(body.get("data") or [])
    if not closed:
        print("Finnhub returned no full market holidays", file=sys.stderr)
        return 1

    errors = validate_provider_overlap(canonical, closed, early_closes)
    write_diagnostic(body, closed, early_closes, errors)
    if errors:
        print("Finnhub market-session feed disagrees with canonical NYSE contract:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        f"Finnhub calendar matches canonical NYSE contract over provider range "
        f"({len(closed)} holidays, {len(early_closes)} early closes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
