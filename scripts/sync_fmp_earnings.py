#!/usr/bin/env python3
"""
Overlay EPS/revenue actuals and estimates from Financial Modeling Prep.

This is a complement to scripts/sync_finnhub_earnings.py, not a replacement.
Finnhub remains the near-term date/timing overlay. FMP is useful because its
earnings calendar response carries revenue actual/estimate fields for the same
announcement-date row shape the frontend already reads.

Default window: today - 30 days through today + 60 days.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from sync_finnhub_earnings import (
    OUTPUT_COLUMNS,
    default_data_dir,
    empty_frame,
    et_today,
    fiscal_q_from_date,
    is_us_symbol,
    load_existing,
    load_local_env,
    normalize_existing,
    number_or_none,
    parse_iso_date,
    write_outputs,
)


BASE_URL = "https://financialmodelingprep.com/stable/earnings-calendar"
REQUEST_DELAY_S = 0.25
MAX_REQUEST_DAYS = 31


class FMPPremiumParameterError(RuntimeError):
    """Raised when the plan rejects a query parameter such as from/to."""


class FMPQuotaError(RuntimeError):
    """Raised when the FMP account has exhausted its daily/request quota."""


def date_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        nxt = min(end, cur + timedelta(days=MAX_REQUEST_DAYS - 1))
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


class CallBudget:
    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.used = 0

    def consume(self) -> None:
        if self.limit is not None and self.used >= self.limit:
            raise RuntimeError(
                f"FMP call budget exhausted ({self.used} >= {self.limit})."
            )
        self.used += 1

    def refund(self) -> None:
        self.used = max(0, self.used - 1)


def get_api_key() -> str | None:
    return (
        os.getenv("FMP_API_KEY")
        or os.getenv("FINANCIAL_MODELING_PREP_API_KEY")
        or os.getenv("FINANCIALMODELINGPREP_API_KEY")
    )


def decode_fmp_response(resp: requests.Response) -> list[dict[str, Any]]:
    if resp.status_code == 429:
        raise FMPQuotaError(f"FMP HTTP 429: {resp.text[:300]}")
    if resp.status_code == 402 and "Premium Query Parameter" in resp.text:
        raise FMPPremiumParameterError(f"FMP HTTP 402: {resp.text[:300]}")
    if not resp.ok:
        raise RuntimeError(f"FMP HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    if isinstance(body, dict) and ("Error Message" in body or "Note" in body):
        text = str(body)
        if "limit" in text.lower() or "quota" in text.lower():
            raise FMPQuotaError(f"FMP response quota error: {text[:300]}")
        raise RuntimeError(f"FMP response error: {text[:300]}")
    if not isinstance(body, list):
        raise RuntimeError(f"Unexpected FMP response: {str(body)[:300]}")
    return [row for row in body if isinstance(row, dict)]


def request_fmp(params: dict[str, str]) -> list[dict[str, Any]]:
    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=60)
        except requests.RequestException as exc:
            if attempt == 2:
                raise RuntimeError(f"FMP request failed: {exc}") from exc
            time.sleep(2**attempt)
            continue
        if resp.status_code == 429 and attempt < 2:
            time.sleep(5 * (attempt + 1))
            continue
        return decode_fmp_response(resp)
    return []


def filter_rows_by_date(
    rows: list[dict[str, Any]],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        try:
            d = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        if start <= d <= end:
            filtered.append(row)
    return filtered


def fetch_fmp_without_date_params(
    start: date,
    end: date,
    api_key: str,
    budget: CallBudget,
    delay_s: float = REQUEST_DELAY_S,
) -> list[dict[str, Any]]:
    budget.consume()
    rows = request_fmp({"apikey": api_key})
    time.sleep(delay_s)
    return filter_rows_by_date(rows, start, end)


def fetch_fmp(
    start: date,
    end: date,
    api_key: str,
    budget: CallBudget,
    delay_s: float = REQUEST_DELAY_S,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk_start, chunk_end in date_chunks(start, end):
        budget.consume()
        params = {
            "from": chunk_start.isoformat(),
            "to": chunk_end.isoformat(),
            "apikey": api_key,
        }
        rows.extend(request_fmp(params))
        time.sleep(delay_s)
    return rows


def normalize_fmp(rows: list[dict[str, Any]]) -> pd.DataFrame:
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not is_us_symbol(symbol):
            continue
        try:
            d = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        out_rows.append(
            {
                "act_symbol": symbol,
                "date": d,
                "timing": "unknown",
                "fiscal_year": d.year,
                "fiscal_q": fiscal_q_from_date(d),
                "eps_actual": number_or_none(row.get("epsActual")),
                "eps_estimate": number_or_none(
                    row.get("epsEstimated", row.get("epsEstimate"))
                ),
                "revenue_actual": number_or_none(row.get("revenueActual")),
                "revenue_estimate": number_or_none(
                    row.get("revenueEstimated", row.get("revenueEstimate"))
                ),
                "source": "fmp",
            }
        )
    if not out_rows:
        return empty_frame()
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS).drop_duplicates(
        ["act_symbol", "date"],
        keep="last",
    )


def source_with_fmp(value: Any) -> str:
    parts = [
        p.strip()
        for p in str(value or "dolthub").split("+")
        if p.strip() and p.strip().lower() != "nan"
    ]
    if "fmp" not in parts:
        parts.append("fmp")
    return "+".join(parts)


def merge_overlay(
    existing: pd.DataFrame,
    overlay: pd.DataFrame,
    *,
    insert_new_events: bool = False,
    overwrite_existing: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]]:
    existing = normalize_existing(existing)
    overlay = normalize_existing(overlay)
    by_key: dict[tuple[str, date], dict[str, Any]] = {
        (row["act_symbol"], row["date"]): row.to_dict()
        for _, row in existing.iterrows()
    }

    stats = {
        "existing_rows": len(existing),
        "fmp_rows": len(overlay),
        "inserted": 0,
        "skipped_new_events": 0,
        "updated": 0,
        "eps_actual_updates": 0,
        "eps_estimate_updates": 0,
        "revenue_actual_updates": 0,
        "revenue_estimate_updates": 0,
    }

    value_cols = [
        "eps_actual",
        "eps_estimate",
        "revenue_actual",
        "revenue_estimate",
    ]
    for _, row in overlay.iterrows():
        key = (row["act_symbol"], row["date"])
        incoming = row.to_dict()
        current = by_key.get(key)
        if current is None:
            if insert_new_events:
                by_key[key] = incoming
                stats["inserted"] += 1
            else:
                stats["skipped_new_events"] += 1
            continue

        changed = False
        for col in value_cols:
            value = incoming.get(col)
            if value is None or pd.isna(value):
                continue
            old = current.get(col)
            if old is None or pd.isna(old) or (overwrite_existing and old != value):
                current[col] = value
                stats[f"{col}_updates"] += 1
                changed = True

        if changed:
            current["source"] = source_with_fmp(current.get("source"))
            stats["updated"] += 1

    merged = pd.DataFrame(by_key.values(), columns=OUTPUT_COLUMNS)
    merged = merged.sort_values(["date", "act_symbol"]).reset_index(drop=True)
    stats["merged_rows"] = len(merged)
    return merged, stats


def write_metadata(data_dir: Path, start: date, end: date, stats: dict[str, Any]) -> None:
    payload = {
        "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "provider": "financialmodelingprep",
        "endpoint": "/stable/earnings-calendar",
        "stats": stats,
    }
    (data_dir / "fmp_earnings_metadata.json").write_text(
        json.dumps(payload, indent=2) + "\n",
    )


def main() -> int:
    load_local_env()
    today = et_today()

    parser = argparse.ArgumentParser(description="Overlay FMP earnings EPS/revenue data")
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, default=None)
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, default=None)
    parser.add_argument("--past-days", type=int, default=30)
    parser.add_argument("--future-days", type=int, default=60)
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_S)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-date-params",
        action="store_true",
        help=(
            "Do not send FMP from/to query parameters. FMP free tier may reject "
            "those parameters; this mode fetches the endpoint default and "
            "filters rows client-side."
        ),
    )
    parser.add_argument(
        "--insert-new-events",
        action="store_true",
        help=(
            "Allow FMP-only earnings dates to be inserted. Default is update-only "
            "to avoid ghost dates when providers disagree."
        ),
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help=(
            "Replace existing EPS/revenue values when FMP disagrees. Default is "
            "fill-missing-only to avoid clobbering Finnhub/DoltHub values."
        ),
    )
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 instead of failing when FMP_API_KEY is missing.",
    )
    parser.add_argument(
        "--allow-quota-exhausted",
        action="store_true",
        help="Exit 0 instead of failing when FMP says the plan quota is exhausted.",
    )
    args = parser.parse_args()

    start = args.from_date or (today - timedelta(days=args.past_days))
    end = args.to_date or (today + timedelta(days=args.future_days))
    if end < start:
        parser.error("--to must be on or after --from")

    api_key = get_api_key()
    if not api_key:
        msg = "FMP_API_KEY missing"
        if args.allow_missing_key:
            print(f"{msg}; skipping FMP earnings overlay")
            return 0
        print(msg, file=sys.stderr)
        return 1

    data_dir = args.data_dir or default_data_dir()
    existing = load_existing(data_dir)
    budget = CallBudget(args.max_calls)

    print(f"FMP earnings overlay: {start} -> {end}")
    fetch_mode = "date_params"
    try:
        if args.no_date_params:
            fetch_mode = "no_date_params"
            raw_rows = fetch_fmp_without_date_params(
                start,
                end,
                api_key,
                budget=budget,
                delay_s=args.delay,
            )
        else:
            raw_rows = fetch_fmp(start, end, api_key, budget=budget, delay_s=args.delay)
    except FMPPremiumParameterError as exc:
        print(f"{exc}", file=sys.stderr)
        print(
            "FMP date parameters are unavailable for this plan; retrying "
            "without from/to and filtering client-side.",
            file=sys.stderr,
        )
        budget.refund()
        fetch_mode = "auto_no_date_params"
        try:
            raw_rows = fetch_fmp_without_date_params(
                start,
                end,
                api_key,
                budget=budget,
                delay_s=args.delay,
            )
        except FMPQuotaError:
            raise
        except RuntimeError as retry_exc:
            print(f"{retry_exc}", file=sys.stderr)
            return 1
    except FMPQuotaError as exc:
        msg = f"{exc}; skipping FMP earnings overlay because quota is exhausted"
        if args.allow_quota_exhausted:
            print(msg)
            return 0
        print(msg, file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    overlay = normalize_fmp(raw_rows)
    merged, stats = merge_overlay(
        existing,
        overlay,
        insert_new_events=args.insert_new_events,
        overwrite_existing=args.overwrite_existing,
    )
    stats["fmp_calls"] = budget.used
    stats["fmp_fetch_mode"] = fetch_mode

    for key, value in stats.items():
        print(f"{key}: {value:,}" if isinstance(value, int) else f"{key}: {value}")

    if args.dry_run:
        print("dry run: no files written")
        return 0

    write_outputs(merged, data_dir)
    write_metadata(data_dir, start, end, stats)
    print(f"wrote {data_dir / 'earnings_calendar.csv'}")
    print(f"wrote {data_dir / 'earnings_calendar.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
