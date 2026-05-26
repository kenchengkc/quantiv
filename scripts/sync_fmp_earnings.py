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


def get_api_key() -> str | None:
    return (
        os.getenv("FMP_API_KEY")
        or os.getenv("FINANCIAL_MODELING_PREP_API_KEY")
        or os.getenv("FINANCIALMODELINGPREP_API_KEY")
    )


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
        for attempt in range(3):
            try:
                resp = requests.get(BASE_URL, params=params, timeout=60)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError(f"FMP request failed: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            if not resp.ok:
                raise RuntimeError(f"FMP HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            if isinstance(body, dict) and ("Error Message" in body or "Note" in body):
                raise RuntimeError(f"FMP response error: {str(body)[:300]}")
            if not isinstance(body, list):
                raise RuntimeError(f"Unexpected FMP response: {str(body)[:300]}")
            rows.extend(r for r in body if isinstance(r, dict))
            break
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
            if old is None or pd.isna(old) or old != value:
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


def write_metadata(data_dir: Path, start: date, end: date, stats: dict[str, int]) -> None:
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
        "--insert-new-events",
        action="store_true",
        help=(
            "Allow FMP-only earnings dates to be inserted. Default is update-only "
            "to avoid ghost dates when providers disagree."
        ),
    )
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 instead of failing when FMP_API_KEY is missing.",
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
    try:
        raw_rows = fetch_fmp(start, end, api_key, budget=budget, delay_s=args.delay)
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    overlay = normalize_fmp(raw_rows)
    merged, stats = merge_overlay(
        existing,
        overlay,
        insert_new_events=args.insert_new_events,
    )
    stats["fmp_calls"] = budget.used

    for key, value in stats.items():
        print(f"{key}: {value:,}")

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
