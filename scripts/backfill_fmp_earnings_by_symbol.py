#!/usr/bin/env python3
"""
Backfill EPS/revenue history from Financial Modeling Prep one symbol at a time.

Some FMP plans allow /stable/earnings?symbol=SYM to return many historical
quarters in one call. This script advances a resumable one-symbol queue under a
daily call budget and merges only missing EPS/revenue fields into
data/earnings_calendar.{csv,parquet} by default.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from sync_finnhub_earnings import (
    default_data_dir,
    et_today,
    is_us_symbol,
    load_existing,
    load_local_env,
    normalize_existing,
    write_outputs,
)
from sync_fmp_earnings import get_api_key, merge_overlay, normalize_fmp


REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
POPULAR_WEIGHTS_PATH = REPO_ROOT / "apps" / "frontend" / "lib" / "popular.ts"
BASE_URL = "https://financialmodelingprep.com/stable/earnings"
STATE_FILENAME = "fmp_earnings_backfill_state.json"
DEFAULT_MAX_CALLS = 240
DEFAULT_DELAY_S = 0.25
DEFAULT_REFRESH_AFTER_DAYS = 90
ENTITLEMENT_KEY = "symbol_endpoint_unavailable"
VALUE_COLUMNS = [
    "eps_actual",
    "eps_estimate",
    "revenue_actual",
    "revenue_estimate",
]
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class FMPRequestError(RuntimeError):
    def __init__(self, message: str, *, stop: bool = False) -> None:
        super().__init__(message)
        self.stop = stop


def is_symbol_endpoint_unavailable(message: str) -> bool:
    text = message.lower()
    return (
        "symbol" in text
        and (
            "premium query parameter" in text
            or "not available under your current subscription" in text
            or "special endpoint" in text
        )
    )


def normalize_symbol(value: Any) -> str | None:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_RE.match(symbol) or not is_us_symbol(symbol):
        return None
    return symbol


def parse_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in value.replace(",", " ").split():
        symbol = normalize_symbol(raw)
        if symbol and symbol not in seen:
            out.append(symbol)
            seen.add(symbol)
    return out


def parse_checked_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load_state(path: Path) -> dict[str, Any]:
    data = load_json(path)
    if isinstance(data, dict) and isinstance(data.get("symbols"), dict):
        return data
    return {"symbols": {}}


def recent_symbol_endpoint_unavailable(
    state: dict[str, Any],
    *,
    now: datetime,
    refresh_after_days: int,
) -> str | None:
    cutoff = now - timedelta(days=refresh_after_days)
    entry = state.get(ENTITLEMENT_KEY)
    if isinstance(entry, dict):
        checked_at = parse_checked_at(entry.get("checked_at"))
        reason = str(entry.get("reason") or "").strip()
        if checked_at and checked_at >= cutoff and reason:
            return reason

    symbols = state.get("symbols")
    if isinstance(symbols, dict):
        for symbol_entry in symbols.values():
            if not isinstance(symbol_entry, dict):
                continue
            checked_at = parse_checked_at(symbol_entry.get("checked_at"))
            error = str(symbol_entry.get("error") or "").strip()
            if (
                checked_at
                and checked_at >= cutoff
                and is_symbol_endpoint_unavailable(error)
            ):
                return error
    return None


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def is_fresh_check(
    state: dict[str, Any],
    symbol: str,
    *,
    now: datetime,
    refresh_after_days: int,
) -> bool:
    symbols = state.get("symbols")
    if not isinstance(symbols, dict):
        return False
    entry = symbols.get(symbol)
    if not isinstance(entry, dict):
        return False
    checked_at = parse_checked_at(entry.get("checked_at"))
    if not checked_at:
        return False
    return checked_at >= now - timedelta(days=refresh_after_days)


def add_symbol_score(scores: dict[str, float], value: Any, weight: float) -> None:
    symbol = normalize_symbol(value)
    if symbol:
        scores[symbol] = scores.get(symbol, 0.0) + weight


def add_event_file_scores(scores: dict[str, float], path: Path, weight: float) -> None:
    data = load_json(path)
    if not isinstance(data, dict):
        return
    events = data.get("events")
    if not isinstance(events, list):
        return
    for event in events:
        if isinstance(event, dict):
            add_symbol_score(
                scores,
                event.get("ticker") or event.get("symbol") or event.get("act_symbol"),
                weight,
            )


def add_popular_scores(scores: dict[str, float], path: Path, weight: float) -> None:
    try:
        text = path.read_text()
    except OSError:
        return
    for symbol, score in re.findall(r'"([A-Z][A-Z0-9.\-]{0,9})":\s*(\d+)', text):
        add_symbol_score(scores, symbol, weight + float(score))


def missing_value_mask(df: pd.DataFrame) -> pd.Series:
    return df[VALUE_COLUMNS].isna().any(axis=1)


def score_missing_symbols(df: pd.DataFrame, *, today: date) -> dict[str, float]:
    df = normalize_existing(df)
    if df.empty:
        return {}

    missing = df[missing_value_mask(df)].copy()
    scores: dict[str, float] = {}
    for symbol, count in missing["act_symbol"].value_counts().items():
        add_symbol_score(scores, symbol, float(count))
    candidate_symbols = set(scores)

    recent_cutoff = today - timedelta(days=730)
    upcoming_end = today + timedelta(days=120)
    for row in missing.itertuples(index=False):
        symbol = normalize_symbol(row.act_symbol)
        if not symbol:
            continue
        event_date = row.date
        if not isinstance(event_date, date):
            continue
        if recent_cutoff <= event_date:
            scores[symbol] = scores.get(symbol, 0.0) + 100.0
        if today <= event_date <= upcoming_end:
            days_until = (event_date - today).days
            scores[symbol] = scores.get(symbol, 0.0) + 10_000.0 + max(0, 120 - days_until)

    add_event_file_scores(scores, PUBLIC_DIR / "weekly.json", 5_000.0)
    add_event_file_scores(scores, PUBLIC_DIR / "screener.json", 2_500.0)
    for path in sorted((PUBLIC_DIR / "weeks").glob("*.json"), reverse=True)[:12]:
        if path.name != "manifest.json":
            add_event_file_scores(scores, path, 1_000.0)
    add_popular_scores(scores, POPULAR_WEIGHTS_PATH, 100.0)
    return {symbol: score for symbol, score in scores.items() if symbol in candidate_symbols}


def select_symbols(
    existing: pd.DataFrame,
    state: dict[str, Any],
    *,
    manual_symbols: list[str],
    today: date,
    now: datetime,
    refresh_after_days: int,
    force: bool,
    max_symbols: int,
    max_calls: int,
) -> tuple[list[str], int]:
    scores = score_missing_symbols(existing, today=today)
    ranked = [
        symbol
        for symbol, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]
    ordered = manual_symbols + [s for s in ranked if s not in set(manual_symbols)]
    pending: list[str] = []
    for symbol in ordered:
        if not force and is_fresh_check(
            state,
            symbol,
            now=now,
            refresh_after_days=refresh_after_days,
        ):
            continue
        pending.append(symbol)
        if len(pending) >= max_symbols:
            break
    return pending[:max_calls], len(pending)


def request_symbol(symbol: str, api_key: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            BASE_URL,
            params={"symbol": symbol, "apikey": api_key},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise FMPRequestError(f"request failed: {exc}") from exc

    if resp.status_code == 429:
        raise FMPRequestError(f"FMP HTTP 429: {resp.text[:240]}", stop=True)
    if resp.status_code == 402:
        text = resp.text[:300]
        stop = (
            "daily" in text.lower()
            or "limit" in text.lower()
            or is_symbol_endpoint_unavailable(text)
        )
        raise FMPRequestError(f"FMP HTTP 402: {text}", stop=stop)
    if not resp.ok:
        raise FMPRequestError(f"FMP HTTP {resp.status_code}: {resp.text[:240]}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise FMPRequestError(f"invalid JSON response: {resp.text[:240]}") from exc
    if isinstance(body, dict) and ("Error Message" in body or "Note" in body):
        text = str(body)[:300]
        stop = any(term in text.lower() for term in ["rate", "daily", "limit"])
        raise FMPRequestError(f"FMP response error: {text}", stop=stop)
    if not isinstance(body, list):
        raise FMPRequestError(f"unexpected FMP response: {str(body)[:240]}")
    return [row for row in body if isinstance(row, dict)]


def state_symbols(state: dict[str, Any]) -> dict[str, Any]:
    symbols = state.get("symbols")
    if not isinstance(symbols, dict):
        symbols = {}
        state["symbols"] = symbols
    return symbols


def update_state_entry(
    state: dict[str, Any],
    symbol: str,
    *,
    checked_at: str,
    ok: bool,
    fetched_rows: int = 0,
    normalized_rows: int = 0,
    error: str | None = None,
) -> None:
    entry = {
        "checked_at": checked_at,
        "ok": ok,
        "fetched_rows": fetched_rows,
        "normalized_rows": normalized_rows,
    }
    if error:
        entry["error"] = error[:500]
    state_symbols(state)[symbol] = entry


def main() -> int:
    load_local_env()
    today = et_today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="", help="Comma/space separated priority symbols")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--max-symbols", type=int, default=10_000)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    parser.add_argument("--refresh-after-days", type=int, default=DEFAULT_REFRESH_AFTER_DAYS)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore fresh state entries")
    parser.add_argument(
        "--insert-new-events",
        action="store_true",
        help="Allow FMP-only dates to be inserted. Default is update-only.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Replace existing EPS/revenue values when FMP disagrees.",
    )
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 instead of failing when FMP_API_KEY is missing.",
    )
    args = parser.parse_args()

    if args.max_calls < 1:
        parser.error("--max-calls must be at least 1")
    if args.max_symbols < 1:
        parser.error("--max-symbols must be at least 1")
    if args.refresh_after_days < 1:
        parser.error("--refresh-after-days must be at least 1")

    api_key = get_api_key()
    if not api_key and not args.dry_run:
        msg = "FMP_API_KEY missing"
        if args.allow_missing_key:
            print(f"{msg}; skipping FMP symbol backfill")
            return 0
        print(msg, file=sys.stderr)
        return 1

    data_dir = args.data_dir or default_data_dir()
    state_path = args.state or (data_dir / STATE_FILENAME)
    existing = load_existing(data_dir)
    state = load_state(state_path)
    now = datetime.now(timezone.utc)
    if not args.force:
        blocked_reason = recent_symbol_endpoint_unavailable(
            state,
            now=now,
            refresh_after_days=args.refresh_after_days,
        )
        if blocked_reason:
            print(
                "FMP symbol earnings backfill skipped: "
                "/stable/earnings?symbol is unavailable on this FMP plan. "
                "Use --force after upgrading the plan to re-probe."
            )
            print(f"reason: {blocked_reason}")
            return 0

    manual_symbols = parse_symbols(args.symbols)
    work, pending_before_budget = select_symbols(
        existing,
        state,
        manual_symbols=manual_symbols,
        today=today,
        now=now,
        refresh_after_days=args.refresh_after_days,
        force=args.force,
        max_symbols=args.max_symbols,
        max_calls=args.max_calls,
    )

    print(
        "FMP symbol earnings backfill: "
        f"{pending_before_budget} pending symbols, budget {args.max_calls}, "
        f"selected {len(work)}"
    )
    if args.dry_run:
        for symbol in work[:50]:
            print(f"would fetch {symbol}")
        if len(work) > 50:
            print(f"... {len(work) - 50} more")
        print("dry run: no API calls made")
        return 0

    overlay_frames: list[pd.DataFrame] = []
    calls_made = 0
    rows_fetched = 0
    stopped_reason: str | None = None
    checked_symbols: list[str] = []

    assert api_key is not None
    for idx, symbol in enumerate(work, start=1):
        print(f"fetching FMP earnings {symbol} ({idx}/{len(work)})", flush=True)
        checked_at = datetime.now(timezone.utc).isoformat()
        calls_made += 1
        checked_symbols.append(symbol)
        try:
            rows = request_symbol(symbol, api_key)
        except FMPRequestError as exc:
            error_text = str(exc)
            update_state_entry(
                state,
                symbol,
                checked_at=checked_at,
                ok=False,
                error=error_text,
            )
            if is_symbol_endpoint_unavailable(error_text):
                state[ENTITLEMENT_KEY] = {
                    "checked_at": checked_at,
                    "reason": error_text[:500],
                }
            print(f"{symbol}: {exc}", file=sys.stderr)
            if exc.stop:
                stopped_reason = error_text
                break
        else:
            rows_fetched += len(rows)
            normalized = normalize_fmp(rows)
            overlay_frames.append(normalized)
            update_state_entry(
                state,
                symbol,
                checked_at=checked_at,
                ok=True,
                fetched_rows=len(rows),
                normalized_rows=len(normalized),
            )
        if idx < len(work):
            time.sleep(args.delay)

    overlay = (
        pd.concat(overlay_frames, ignore_index=True)
        if overlay_frames
        else normalize_fmp([])
    )
    merged, stats = merge_overlay(
        existing,
        overlay,
        insert_new_events=args.insert_new_events,
        overwrite_existing=args.overwrite_existing,
    )
    stats.update(
        {
            "calls_made": calls_made,
            "rows_fetched": rows_fetched,
            "symbols_checked": len(checked_symbols),
            "pending_before_budget": pending_before_budget,
            "selected_symbols": len(work),
        }
    )

    state["generated_at"] = datetime.now(timezone.utc).isoformat()
    state["provider"] = "financialmodelingprep"
    state["endpoint"] = "/stable/earnings"
    state["last_run"] = {
        **stats,
        "stopped_reason": stopped_reason,
    }
    save_state(state_path, state)
    write_outputs(merged, data_dir)

    for key, value in stats.items():
        print(f"{key}: {value:,}" if isinstance(value, int) else f"{key}: {value}")
    if stopped_reason:
        print(f"stopped_reason: {stopped_reason}", file=sys.stderr)
    print(f"wrote {data_dir / 'earnings_calendar.csv'}")
    print(f"wrote {data_dir / 'earnings_calendar.parquet'}")
    print(f"wrote {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
