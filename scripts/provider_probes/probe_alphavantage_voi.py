#!/usr/bin/env python3
"""
Probe Alpha Vantage volume-to-open-interest ratio coverage and entitlement.

Default mode checks the historical endpoint only. Use --include-realtime for a
second realtime probe. The output is a persistent audit JSON file; it is not
consumed by the app or the ML pipeline until coverage looks good enough to
productize.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "data" / "research" / "provider_probes" / "alpha_vantage_voi_probe.json"
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
POPULAR_WEIGHTS_PATH = REPO_ROOT / "apps" / "frontend" / "lib" / "popular.ts"
BASE_URL = "https://www.alphavantage.co/query"
HISTORICAL_FUNCTION = "HISTORICAL_VOLUME_OPEN_INTEREST_RATIO"
REALTIME_FUNCTION = "REALTIME_VOLUME_OPEN_INTEREST_RATIO"
DEFAULT_DELAY_S = 15.0
DEFAULT_MAX_CALLS = 5
DEFAULT_REFRESH_AFTER_DAYS = 30
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def load_local_env() -> None:
    for path in [
        REPO_ROOT / "config" / ".env.local",
        REPO_ROOT / "config" / ".env.production",
    ]:
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_api_key() -> str | None:
    return os.getenv("ALPHAVANTAGE_API_KEY")


def normalize_symbol(value: Any) -> str | None:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_RE.match(symbol):
        return None
    return symbol


def parse_symbols(value: str) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in value.replace(",", " ").split():
        symbol = normalize_symbol(raw)
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def add_symbol_score(scores: dict[str, int], value: Any, weight: int) -> None:
    symbol = normalize_symbol(value)
    if symbol:
        scores[symbol] = scores.get(symbol, 0) + weight


def add_event_symbols(scores: dict[str, int], path: Path, weight: int) -> None:
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


def add_popular_symbols(scores: dict[str, int], path: Path, weight: int) -> None:
    try:
        text = path.read_text()
    except OSError:
        return
    for symbol, score in re.findall(r'"([A-Z][A-Z0-9.\-]{0,9})":\s*(\d+)', text):
        add_symbol_score(scores, symbol, weight + int(score))


def priority_symbols(universe: str) -> list[str]:
    scores: dict[str, int] = {}
    if universe in {"priority", "frontend"}:
        add_event_symbols(scores, PUBLIC_DIR / "weekly.json", 10_000)
        add_event_symbols(scores, PUBLIC_DIR / "screener.json", 5_000)
        for path in sorted((PUBLIC_DIR / "weeks").glob("*.json"), reverse=True):
            if path.name == "manifest.json":
                continue
            add_event_symbols(scores, path, 2_000)

    if universe in {"priority", "popular"}:
        add_popular_symbols(scores, POPULAR_WEIGHTS_PATH, 100)

    return [
        symbol
        for symbol, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]


def fetch(function: str, symbol: str, api_key: str) -> dict[str, Any]:
    resp = requests.get(
        BASE_URL,
        params={"function": function, "symbol": symbol, "apikey": api_key},
        timeout=60,
    )
    if not resp.ok:
        return {"_http_error": resp.status_code, "_body": resp.text[:300]}
    body = resp.json()
    return body if isinstance(body, dict) else {"_unexpected": body}


def extract_rows(body: dict[str, Any]) -> list[Any]:
    data = body.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    for value in body.values():
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = list(value.values())
            if nested and all(isinstance(item, dict) for item in nested[:5]):
                return nested
    return []


def summarize_body(body: dict[str, Any]) -> dict[str, Any]:
    rows = extract_rows(body)
    error = (
        body.get("Error Message")
        or body.get("Information")
        or body.get("Note")
        or body.get("_http_error")
    )
    sample = rows[0] if rows and isinstance(rows[0], dict) else None
    return {
        "ok": bool(rows) and not error,
        "rows": len(rows),
        "error": str(error)[:240] if error else None,
        "top_level_keys": sorted(str(k) for k in body.keys())[:20],
        "sample_keys": sorted(str(k) for k in sample.keys()) if sample else [],
    }


def load_state(path: Path) -> dict[str, Any]:
    data = load_json(path)
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return data
    return {"results": []}


def result_key(result: dict[str, Any]) -> tuple[str, str] | None:
    symbol = normalize_symbol(result.get("symbol"))
    function = str(result.get("function") or "")
    if not symbol or function not in {HISTORICAL_FUNCTION, REALTIME_FUNCTION}:
        return None
    return (symbol, function)


def parse_checked_at(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_fresh_result(
    result: dict[str, Any] | None,
    *,
    now: datetime,
    refresh_after_days: int,
) -> bool:
    if not result:
        return False
    checked_at = parse_checked_at(result.get("checked_at"))
    if not checked_at:
        return False
    return checked_at >= now - timedelta(days=refresh_after_days)


def existing_results_by_key(state: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for result in state.get("results", []):
        if not isinstance(result, dict):
            continue
        key = result_key(result)
        if key:
            out[key] = result
    return out


def select_work(
    symbols: list[str],
    functions: list[str],
    results_by_key: dict[tuple[str, str], dict[str, Any]],
    *,
    now: datetime,
    refresh_after_days: int,
    max_calls: int,
) -> tuple[list[tuple[str, str]], int]:
    pending: list[tuple[str, str]] = []
    for symbol in symbols:
        for function in functions:
            key = (symbol, function)
            if is_fresh_result(
                results_by_key.get(key),
                now=now,
                refresh_after_days=refresh_after_days,
            ):
                continue
            pending.append(key)
    return pending[:max_calls], len(pending)


def should_stop_after_summary(summary: dict[str, Any]) -> bool:
    error = str(summary.get("error") or "").lower()
    if not error:
        return False
    stop_terms = ["api call frequency", "daily", "minute", "rate limit", "premium"]
    return any(term in error for term in stop_terms)


def build_symbol_list(args: argparse.Namespace) -> list[str]:
    manual = parse_symbols(args.symbols)
    if args.universe == "manual":
        return manual[: args.max_symbols]

    symbols = manual + priority_symbols(args.universe)
    seen: set[str] = set()
    deduped: list[str] = []
    for symbol in symbols:
        if symbol not in seen:
            deduped.append(symbol)
            seen.add(symbol)
    return deduped[: args.max_symbols]


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default="",
        help=(
            "Comma/space separated symbols. Prepended to the selected universe; "
            "used alone when --universe manual is selected."
        ),
    )
    parser.add_argument(
        "--universe",
        choices=["priority", "frontend", "popular", "manual"],
        default="priority",
        help="Symbol source for multi-day coverage probing.",
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    parser.add_argument("--max-symbols", type=int, default=250)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument(
        "--refresh-after-days",
        type=int,
        default=DEFAULT_REFRESH_AFTER_DAYS,
        help="Skip symbol/function pairs checked within this many days.",
    )
    parser.add_argument("--include-realtime", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 instead of failing when ALPHAVANTAGE_API_KEY is missing.",
    )
    args = parser.parse_args()
    if args.max_calls < 1:
        parser.error("--max-calls must be at least 1")
    if args.max_symbols < 1:
        parser.error("--max-symbols must be at least 1")
    if args.refresh_after_days < 1:
        parser.error("--refresh-after-days must be at least 1")

    now = datetime.now(timezone.utc)
    symbols = build_symbol_list(args)
    functions = [HISTORICAL_FUNCTION]
    if args.include_realtime:
        functions.append(REALTIME_FUNCTION)

    state = load_state(args.output)
    results_by_key = existing_results_by_key(state)
    work, pending_before_budget = select_work(
        symbols,
        functions,
        results_by_key,
        now=now,
        refresh_after_days=args.refresh_after_days,
        max_calls=args.max_calls,
    )

    print(
        "Alpha Vantage V/OI probe: "
        f"{len(symbols)} candidate symbols, {pending_before_budget} pending "
        f"symbol/function checks, budget {args.max_calls} calls"
    )
    if args.dry_run:
        for symbol, function in work:
            print(f"would probe {function} {symbol}")
        print("dry run: no API calls made")
        return 0

    api_key = get_api_key()
    if not api_key:
        msg = "ALPHAVANTAGE_API_KEY missing"
        if args.allow_missing_key:
            print(f"{msg}; skipping Alpha Vantage V/OI probe")
            return 0
        print(msg, file=sys.stderr)
        return 1

    calls_made = 0
    stopped_reason: str | None = None
    for idx, (symbol, function) in enumerate(work, start=1):
        print(f"probing {function} {symbol} ({idx}/{len(work)})", flush=True)
        body = fetch(function, symbol, api_key)
        summary = summarize_body(body)
        checked_at = datetime.now(timezone.utc).isoformat()
        result = {"symbol": symbol, "function": function, "checked_at": checked_at, **summary}
        results_by_key[(symbol, function)] = result
        calls_made += 1
        if should_stop_after_summary(summary):
            stopped_reason = str(summary.get("error") or "provider limit")
            print(f"stopping early: {stopped_reason}")
            break
        if idx < len(work):
            time.sleep(args.delay)

    results = [
        result
        for _key, result in sorted(
            results_by_key.items(),
            key=lambda item: (item[0][0], item[0][1]),
        )
    ]
    historical = [r for r in results if r["function"] == HISTORICAL_FUNCTION]
    realtime = [r for r in results if r["function"] == REALTIME_FUNCTION]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "alphavantage",
        "purpose": "volume_to_open_interest_ratio_probe",
        "universe": args.universe,
        "symbol_count": len(symbols),
        "pending_before_budget": pending_before_budget,
        "calls_made": calls_made,
        "max_calls": args.max_calls,
        "delay_seconds": args.delay,
        "refresh_after_days": args.refresh_after_days,
        "stopped_reason": stopped_reason,
        "historical_ok": sum(1 for r in historical if r["ok"]),
        "historical_checked": len(historical),
        "realtime_ok": sum(1 for r in realtime if r["ok"]),
        "realtime_checked": len(realtime),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        display_path = args.output.relative_to(REPO_ROOT)
    except ValueError:
        display_path = args.output
    print(f"wrote {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
