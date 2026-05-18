#!/usr/bin/env python3
"""
Overlay near-term earnings calendar data from Finnhub onto the existing
DoltHub earnings baseline.

Why this exists:
  - DoltHub remains the long-history source used by historical move features.
  - Finnhub's free earnings calendar gives fresher near-term dates/timing and
    EPS/revenue estimates, but its free historical entitlement is limited.
  - We merge a rolling window into data/earnings_calendar.{csv,parquet} so the
    rest of the pipeline keeps reading the same files.

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
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
BASE_URL = "https://finnhub.io/api/v1/calendar/earnings"
REQUEST_DELAY_S = 0.25
MAX_REQUEST_DAYS = 31

OUTPUT_COLUMNS = [
    "act_symbol",
    "date",
    "timing",
    "fiscal_year",
    "fiscal_q",
    "eps_actual",
    "eps_estimate",
    "revenue_actual",
    "revenue_estimate",
    "source",
]

ARROW_SCHEMA = pa.schema(
    [
        ("act_symbol", pa.string()),
        ("date", pa.date32()),
        ("timing", pa.string()),
        ("fiscal_year", pa.int64()),
        ("fiscal_q", pa.string()),
        ("eps_actual", pa.float64()),
        ("eps_estimate", pa.float64()),
        ("revenue_actual", pa.float64()),
        ("revenue_estimate", pa.float64()),
        ("source", pa.string()),
    ]
)


def load_local_env() -> None:
    """Local dev convenience. CI/hosts still win via real environment vars."""
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


def et_today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def default_data_dir() -> Path:
    env_value = os.getenv("DATA_DIR")
    if env_value:
        configured = Path(env_value).expanduser()
        if configured.exists() or not DEFAULT_DATA_DIR.exists():
            return configured
        print(
            f"⚠ DATA_DIR points to missing path {configured}; "
            f"using repo data dir {DEFAULT_DATA_DIR}",
            file=sys.stderr,
        )
    return DEFAULT_DATA_DIR


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid YYYY-MM-DD date: {value}") from exc


def normalize_timing(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in {"bmo", "before_market_open", "before open", "before market open"}:
        return "bmo"
    if s in {"amc", "after_market_close", "after close", "after market close"}:
        return "amc"
    if s in {"dmh", "during_market_hours", "during market hour", "during market hours"}:
        return "dmh"
    return "unknown"


def fiscal_q_from_date(d: date) -> str:
    if d.month <= 3:
        return "Q1"
    if d.month <= 6:
        return "Q2"
    if d.month <= 9:
        return "Q3"
    return "Q4"


def fiscal_q_from_finnhub(row: dict[str, Any], fallback: date) -> str:
    q = row.get("quarter")
    try:
        qi = int(q)
        if 1 <= qi <= 4:
            return f"Q{qi}"
    except (TypeError, ValueError):
        pass
    return fiscal_q_from_date(fallback)


def number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(out):
        return None
    return out


def int_or_none(value: Any) -> int | None:
    n = number_or_none(value)
    if n is None:
        return None
    return int(n)


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def normalize_existing(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return empty_frame()

    out = df.copy()
    if "symbol" in out.columns and "act_symbol" not in out.columns:
        out = out.rename(columns={"symbol": "act_symbol"})
    if "hour" in out.columns and "timing" not in out.columns:
        out = out.rename(columns={"hour": "timing"})
    rename_map = {
        "epsActual": "eps_actual",
        "epsEstimate": "eps_estimate",
        "revenueActual": "revenue_actual",
        "revenueEstimate": "revenue_estimate",
        "year": "fiscal_year",
    }
    out = out.rename(columns={k: v for k, v in rename_map.items() if k in out.columns})

    if "act_symbol" not in out.columns or "date" not in out.columns:
        raise ValueError("Existing earnings file must contain act_symbol and date columns")

    out["act_symbol"] = out["act_symbol"].astype(str).str.strip().str.upper()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out = out[out["act_symbol"].ne("") & out["date"].notna()].copy()
    if "timing" not in out.columns:
        out["timing"] = "unknown"
    out["timing"] = out["timing"].map(normalize_timing)

    if "fiscal_year" not in out.columns:
        out["fiscal_year"] = pd.NA
    fiscal_years: list[int] = []
    for v, d in zip(out["fiscal_year"], out["date"]):
        parsed = int_or_none(v)
        fiscal_years.append(parsed if parsed is not None else d.year)
    out["fiscal_year"] = fiscal_years

    if "fiscal_q" not in out.columns:
        out["fiscal_q"] = [fiscal_q_from_date(d) for d in out["date"]]
    else:
        out["fiscal_q"] = [
            str(q).strip().upper() if str(q).strip().upper() in {"Q1", "Q2", "Q3", "Q4"} else fiscal_q_from_date(d)
            for q, d in zip(out["fiscal_q"], out["date"])
        ]

    for col in ["eps_actual", "eps_estimate", "revenue_actual", "revenue_estimate"]:
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = out[col].map(number_or_none)

    if "source" not in out.columns:
        out["source"] = "dolthub"
    else:
        out["source"] = out["source"].fillna("dolthub").astype(str)

    return out[OUTPUT_COLUMNS].drop_duplicates(["act_symbol", "date"], keep="last")


def load_existing(data_dir: Path) -> pd.DataFrame:
    csv_path = data_dir / "earnings_calendar.csv"
    parquet_path = data_dir / "earnings_calendar.parquet"
    if csv_path.exists():
        # keep_default_na=False is important: "NA" is a valid ticker.
        return normalize_existing(pd.read_csv(csv_path, keep_default_na=False))
    if parquet_path.exists():
        return normalize_existing(pd.read_parquet(parquet_path))
    return empty_frame()


def date_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        nxt = min(end, cur + timedelta(days=MAX_REQUEST_DAYS - 1))
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


class CallBudget:
    """Defensive cap on Finnhub HTTP calls so a wide --symbols run can't
    accidentally exhaust the 60-calls/min ceiling shared with intraday
    quote workers. None = unlimited (legacy behavior)."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.used = 0

    def consume(self) -> None:
        if self.limit is not None and self.used >= self.limit:
            raise RuntimeError(
                f"Finnhub call budget exhausted ({self.used} >= {self.limit}). "
                "Raise --max-calls or narrow --symbols / date range."
            )
        self.used += 1


def fetch_finnhub(
    start: date,
    end: date,
    token: str,
    symbol: str | None = None,
    budget: CallBudget | None = None,
    delay_s: float = REQUEST_DELAY_S,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk_start, chunk_end in date_chunks(start, end):
        params: dict[str, Any] = {
            "from": chunk_start.isoformat(),
            "to": chunk_end.isoformat(),
            "token": token,
        }
        if symbol:
            params["symbol"] = symbol.upper()
        if budget is not None:
            budget.consume()
        for attempt in range(3):
            try:
                resp = requests.get(BASE_URL, params=params, timeout=60)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError(f"Finnhub request failed: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429 and attempt < 2:
                # Backoff doubles each retry; 5s → 10s. Free tier is 60/min,
                # so a single 429 means we briefly exceeded — recover quickly.
                time.sleep(5 * (attempt + 1))
                continue
            if not resp.ok:
                raise RuntimeError(f"Finnhub HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            rows.extend(body.get("earningsCalendar") or [])
            break
        time.sleep(delay_s)
    return rows


def parse_symbol_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip().upper() for p in value.replace(",", " ").split()]
    return [p for p in parts if p]


def normalize_finnhub(rows: list[dict[str, Any]]) -> pd.DataFrame:
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            d = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        fiscal_year = int_or_none(row.get("year")) or d.year
        out_rows.append(
            {
                "act_symbol": symbol,
                "date": d,
                "timing": normalize_timing(row.get("hour")),
                "fiscal_year": fiscal_year,
                "fiscal_q": fiscal_q_from_finnhub(row, d),
                "eps_actual": number_or_none(row.get("epsActual")),
                "eps_estimate": number_or_none(row.get("epsEstimate")),
                "revenue_actual": number_or_none(row.get("revenueActual")),
                "revenue_estimate": number_or_none(row.get("revenueEstimate")),
                "source": "finnhub",
            }
        )
    if not out_rows:
        return empty_frame()
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS).drop_duplicates(
        ["act_symbol", "date"],
        keep="last",
    )


def merge_overlay(existing: pd.DataFrame, overlay: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    existing = normalize_existing(existing)
    overlay = normalize_existing(overlay)
    by_key: dict[tuple[str, date], dict[str, Any]] = {
        (row["act_symbol"], row["date"]): row.to_dict()
        for _, row in existing.iterrows()
    }

    stats = {
        "existing_rows": len(existing),
        "finnhub_rows": len(overlay),
        "inserted": 0,
        "updated": 0,
        "timing_updates": 0,
    }

    for _, row in overlay.iterrows():
        key = (row["act_symbol"], row["date"])
        incoming = row.to_dict()
        current = by_key.get(key)
        if current is None:
            by_key[key] = incoming
            stats["inserted"] += 1
            continue

        changed = False
        incoming_timing = incoming.get("timing", "unknown")
        if incoming_timing != "unknown" and incoming_timing != current.get("timing"):
            current["timing"] = incoming_timing
            stats["timing_updates"] += 1
            changed = True

        for col in [
            "fiscal_year",
            "fiscal_q",
            "eps_actual",
            "eps_estimate",
            "revenue_actual",
            "revenue_estimate",
        ]:
            value = incoming.get(col)
            if value is not None and not pd.isna(value) and value != current.get(col):
                current[col] = value
                changed = True

        if changed:
            current["source"] = "dolthub+finnhub"
            stats["updated"] += 1

    merged = pd.DataFrame(by_key.values(), columns=OUTPUT_COLUMNS)
    merged = merged.sort_values(["date", "act_symbol"]).reset_index(drop=True)
    stats["merged_rows"] = len(merged)
    return merged, stats


def write_outputs(df: pd.DataFrame, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    out = normalize_existing(df).sort_values(["date", "act_symbol"]).reset_index(drop=True)

    csv_df = out.copy()
    csv_df["date"] = csv_df["date"].map(lambda d: d.isoformat())
    csv_df.to_csv(data_dir / "earnings_calendar.csv", index=False)

    parquet_df = out.copy()
    table = pa.Table.from_pandas(parquet_df, schema=ARROW_SCHEMA, preserve_index=False)
    pq.write_table(table, data_dir / "earnings_calendar.parquet", compression="snappy")


def write_metadata(data_dir: Path, start: date, end: date, stats: dict[str, int]) -> None:
    payload = {
        "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "stats": stats,
    }
    (data_dir / "finnhub_earnings_metadata.json").write_text(
        json.dumps(payload, indent=2) + "\n",
    )


def main() -> int:
    load_local_env()
    today = et_today()

    parser = argparse.ArgumentParser(description="Overlay near-term Finnhub earnings onto DoltHub baseline")
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, default=None)
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, default=None)
    parser.add_argument("--past-days", type=int, default=30)
    parser.add_argument("--future-days", type=int, default=60)
    parser.add_argument("--symbol", help="Optional single-symbol probe/sync")
    parser.add_argument(
        "--symbols",
        help=(
            "Comma- or space-separated list of symbols. When set, runs a "
            "per-symbol query for each (useful for deep backfill on a "
            "curated universe). Mutually exclusive with --symbol."
        ),
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help=(
            "Defensive ceiling on Finnhub HTTP calls in this run. "
            "Each chunked request counts as one. Default: unlimited. "
            "Set this to leave headroom for intraday quote workers (60/min)."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY_S,
        help=(
            "Seconds to sleep between Finnhub HTTP calls. Default "
            f"{REQUEST_DELAY_S}s (≈ {int(60 / REQUEST_DELAY_S)}/min). "
            "Use 1.05 for full-universe sweeps to stay under the 60/min "
            "free-tier rate limit with margin (≈ 57/min)."
        ),
    )
    parser.add_argument(
        "--all-recent",
        action="store_true",
        help=(
            "Replace --symbols with every ticker that has had an earnings "
            "event in the last 2 years. Used for the weekly full-universe "
            "Sunday sweep: catches the last reported event for every active "
            "US ticker, which the broad daily fetch misses for anyone who "
            "reported >~7 days ago."
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 instead of failing when FINNHUB_API_KEY is missing (useful in CI until the secret is added).",
    )
    args = parser.parse_args()

    mutually_exclusive = sum(bool(x) for x in (args.symbol, args.symbols, args.all_recent))
    if mutually_exclusive > 1:
        parser.error("--symbol, --symbols, and --all-recent are mutually exclusive")

    start = args.from_date or (today - timedelta(days=args.past_days))
    end = args.to_date or (today + timedelta(days=args.future_days))
    if end < start:
        parser.error("--to must be on or after --from")

    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        msg = "FINNHUB_API_KEY missing"
        if args.allow_missing_key:
            print(f"⚠ {msg}; skipping Finnhub earnings overlay")
            return 0
        print(f"✗ {msg}", file=sys.stderr)
        return 1

    data_dir = args.data_dir or default_data_dir()
    existing = load_existing(data_dir)

    symbols = parse_symbol_list(args.symbols)
    if args.symbol:
        symbols = [args.symbol.upper()]
    if args.all_recent:
        # Anyone who has reported in the last 2 years is treated as "active"
        # for the purposes of the weekly sweep. Older-only tickers are
        # almost certainly delisted; not worth the API calls.
        recent_cutoff = today - timedelta(days=730)
        symbols = sorted(
            existing.loc[existing["date"] >= recent_cutoff, "act_symbol"]
            .dropna()
            .unique()
            .tolist()
        )
        if not symbols:
            print("⚠ --all-recent: no tickers found in existing earnings_calendar", file=sys.stderr)
            return 1

    print(f"Finnhub earnings overlay: {start} → {end}")
    if symbols:
        eta_s = len(symbols) * args.delay
        print(
            f"per-symbol mode: {len(symbols)} symbols "
            f"({', '.join(symbols[:8])}{'…' if len(symbols) > 8 else ''})"
            f"  delay={args.delay}s  ETA≈{eta_s / 60:.0f} min"
        )
    data_dir = args.data_dir or default_data_dir()

    budget = CallBudget(args.max_calls)

    raw_rows: list[dict[str, Any]] = []
    try:
        if symbols:
            for i, sym in enumerate(symbols, 1):
                if i == 1 or i % 100 == 0 or i == len(symbols):
                    print(f"  [{i}/{len(symbols)}] {sym} (calls used: {budget.used})",
                          flush=True)
                raw_rows.extend(
                    fetch_finnhub(start, end, token, sym, budget=budget, delay_s=args.delay)
                )
        else:
            raw_rows = fetch_finnhub(start, end, token, None, budget=budget, delay_s=args.delay)
    except RuntimeError as exc:
        print(f"⚠ {exc}", file=sys.stderr)
        if budget.used == 0:
            return 1
        print("  proceeding with partial data already fetched")
    overlay = normalize_finnhub(raw_rows)
    merged, stats = merge_overlay(existing, overlay)
    stats["finnhub_calls"] = budget.used

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
