#!/usr/bin/env python3
"""
Sync options chain data from DoltHub (post-no-preference/options) to local Parquet files.

DoltHub API constraints (free tier):
  - 1 000 row limit per query
  - Queries on the 105M-row table time out for full-table scans (COUNT, MIN/MAX, ORDER BY)
  - Must use tight indexed WHERE clauses: date + act_symbol range

Strategy:
  - Paginate each date by symbol-prefix buckets (A-B, B-C, ... Z+)
  - Within each bucket, paginate via LIMIT 1000 + keyset on (act_symbol, expiration, strike, call_put)
  - Write one Parquet file per date: data/parquet/options_chain/year=YYYY/month=MM/YYYY-MM-DD.parquet

Usage:
  # Full load for a date range
  python scripts/sync_dolthub.py --full --start-date 2023-01-01 --end-date 2023-01-31

  # Incremental (days since last sync, default 3)
  python scripts/sync_dolthub.py

  # Single date
  python scripts/sync_dolthub.py --date 2026-04-10

  # Dry run
  python scripts/sync_dolthub.py --dry-run
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ---------------------------------------------------------------------------
# DoltHub API config
# ---------------------------------------------------------------------------
DOLTHUB_OWNER = "post-no-preference"
DOLTHUB_BRANCH = "master"
ROW_LIMIT = 1000  # DoltHub enforced max

# API endpoints for each repo
OPTIONS_API = f"https://www.dolthub.com/api/v1alpha1/{DOLTHUB_OWNER}/options/{DOLTHUB_BRANCH}"
EARNINGS_API = f"https://www.dolthub.com/api/v1alpha1/{DOLTHUB_OWNER}/earnings/{DOLTHUB_BRANCH}"
STOCKS_API = f"https://www.dolthub.com/api/v1alpha1/{DOLTHUB_OWNER}/stocks/{DOLTHUB_BRANCH}"

COLUMNS = "date, act_symbol, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega, rho"

# Symbol-prefix buckets for pagination (covers A-Z and a few common non-alpha)
SYMBOL_BUCKETS = [
    ("A", "D"), ("D", "G"), ("G", "K"), ("K", "N"),
    ("N", "R"), ("R", "U"), ("U", "Zz"),
]

# Arrow schema for consistent Parquet writes
ARROW_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("act_symbol", pa.string()),
    ("expiration", pa.date32()),
    ("strike", pa.float64()),
    ("call_put", pa.string()),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("vol", pa.float64()),
    ("delta", pa.float64()),
    ("gamma", pa.float64()),
    ("theta", pa.float64()),
    ("vega", pa.float64()),
    ("rho", pa.float64()),
])

COLUMN_LIST = [f.name for f in ARROW_SCHEMA]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
SYMBOLS_DIR = REPO_ROOT / "apps" / "frontend" / "public" / "symbols"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from delisted import (  # noqa: E402
    canonical_ticker,
    delisted_tickers,
    is_delisted,
    ticker_renames,
)
METADATA_FILE = "sync_metadata.json"
API_DELAY = 0.3  # seconds between API calls


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))


def parquet_root() -> Path:
    return data_dir() / "parquet" / "options_chain"


def metadata_path() -> Path:
    return data_dir() / METADATA_FILE


# ---------------------------------------------------------------------------
# DoltHub query with retries
# ---------------------------------------------------------------------------
def query(sql: str, api_url: str = OPTIONS_API, retries: int = 3) -> list[dict]:
    """Run a SQL query against DoltHub. Returns list of row dicts."""
    for attempt in range(retries):
        try:
            resp = requests.get(api_url, params={"q": sql}, timeout=180)
            resp.raise_for_status()
            body = resp.json()
            status = body.get("query_execution_status", "")
            if status == "Success":
                return body.get("rows", [])
            if status == "RowLimit":
                # Hit the 1000-row cap — return what we got
                return body.get("rows", [])
            msg = body.get("query_execution_message", status)
            raise RuntimeError(f"DoltHub: {msg}")
        except (requests.RequestException, RuntimeError) as exc:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  ⚠ attempt {attempt+1} failed ({exc}), retry in {wait}s")
                time.sleep(wait)
            else:
                raise
    return []


# ---------------------------------------------------------------------------
# Fetch all rows for a single date using bucketed pagination
# ---------------------------------------------------------------------------
def fetch_date(target: date) -> pd.DataFrame:
    """Fetch every row for *target* from DoltHub, handling the 1000-row cap."""
    ds = target.isoformat()
    all_rows: list[dict] = []

    for lo, hi in SYMBOL_BUCKETS:
        # Keyset pagination within the bucket
        last_key: Optional[tuple] = None
        while True:
            if last_key is None:
                where = f"date = '{ds}' AND act_symbol >= '{lo}' AND act_symbol < '{hi}'"
            else:
                sym, exp, stk, cp = last_key
                # Continue after the last row we saw
                where = (
                    f"date = '{ds}' AND act_symbol >= '{lo}' AND act_symbol < '{hi}' "
                    f"AND (act_symbol, expiration, strike, call_put) > ('{sym}', '{exp}', {stk}, '{cp}')"
                )

            sql = f"SELECT {COLUMNS} FROM option_chain WHERE {where} LIMIT {ROW_LIMIT}"
            rows = query(sql)
            if not rows:
                break

            all_rows.extend(rows)

            if len(rows) < ROW_LIMIT:
                break  # No more pages in this bucket

            # Set keyset for next page
            last = rows[-1]
            last_key = (last["act_symbol"], last["expiration"], last["strike"], last["call_put"])
            time.sleep(API_DELAY)

        time.sleep(API_DELAY)

    if not all_rows:
        return pd.DataFrame(columns=COLUMN_LIST)

    df = pd.DataFrame(all_rows)
    for col in COLUMN_LIST:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMN_LIST]

    # Type coercion
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiration"] = pd.to_datetime(df["expiration"]).dt.date
    for c in ["strike", "bid", "ask", "vol", "delta", "gamma", "theta", "vega", "rho"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Parquet write
# ---------------------------------------------------------------------------
def write_date(df: pd.DataFrame, root: Path) -> int:
    """Write a single-date DataFrame to its Parquet partition file."""
    if df.empty:
        return 0
    dt = df["date"].iloc[0]
    dt_obj = dt if isinstance(dt, date) else pd.Timestamp(dt).date()
    out_dir = root / f"year={dt_obj.year}" / f"month={dt_obj.month:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{dt_obj.isoformat()}.parquet"
    table = pa.Table.from_pandas(df, schema=ARROW_SCHEMA, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")
    return len(df)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def load_meta() -> dict:
    p = metadata_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_meta(meta: dict):
    p = metadata_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=2, default=str))


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------
def trading_dates(start: date, end: date) -> list[date]:
    """Generate weekday dates in [start, end]."""
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d)
        d += timedelta(days=1)
    return out


def latest_dolthub_date() -> date:
    """Get the most recent date in DoltHub (fast — uses indexed ORDER BY DESC LIMIT 1)."""
    rows = query("SELECT date FROM option_chain ORDER BY date DESC LIMIT 1", OPTIONS_API)
    if rows:
        return date.fromisoformat(rows[0]["date"])
    raise RuntimeError("Could not get latest date from DoltHub")


def earliest_dolthub_date() -> date:
    """Get the earliest date in DoltHub."""
    rows = query("SELECT date FROM option_chain ORDER BY date ASC LIMIT 1", OPTIONS_API)
    if rows:
        return date.fromisoformat(rows[0]["date"])
    raise RuntimeError("Could not get earliest date from DoltHub")


def already_synced(target: date, root: Path) -> bool:
    """Check if a Parquet file already exists for this date."""
    dt_obj = target
    path = root / f"year={dt_obj.year}" / f"month={dt_obj.month:02d}" / f"{dt_obj.isoformat()}.parquet"
    return path.exists()


# ---------------------------------------------------------------------------
# Sync modes
# ---------------------------------------------------------------------------
def sync_dates(dates: list[date], root: Path, skip_existing: bool = True) -> int:
    """Sync a list of dates. Returns total rows written."""
    total = 0
    for i, d in enumerate(dates):
        if skip_existing and already_synced(d, root):
            print(f"  [{i+1}/{len(dates)}] {d} — already exists, skipping")
            continue

        print(f"  [{i+1}/{len(dates)}] {d} — fetching ...", end=" ", flush=True)
        try:
            df = fetch_date(d)
            n = write_date(df, root)
            total += n
            print(f"{n:,} rows")
        except Exception as exc:
            print(f"FAILED: {exc}")
            # Continue with next date instead of aborting
            continue

    return total


def cmd_full(args):
    """Full sync over a date range."""
    start = date.fromisoformat(args.start_date) if args.start_date else earliest_dolthub_date()
    end = date.fromisoformat(args.end_date) if args.end_date else latest_dolthub_date()
    print(f"{'='*60}\nFULL SYNC: {start} → {end}\n{'='*60}")

    dates = trading_dates(start, end)
    print(f"{len(dates)} trading days to sync\n")

    root = parquet_root()
    root.mkdir(parents=True, exist_ok=True)
    total = sync_dates(dates, root, skip_existing=not args.force)

    save_meta({
        "last_sync_date": end.isoformat(),
        "last_sync_time": datetime.now().isoformat(),
        "total_rows_synced": load_meta().get("total_rows_synced", 0) + total,
        "mode": "full",
    })
    print(f"\n✅ Full sync: {total:,} rows written")


def cmd_incremental(args):
    """Incremental sync since last sync date."""
    meta = load_meta()
    last = meta.get("last_sync_date")
    end = latest_dolthub_date()

    if last:
        start = date.fromisoformat(last) + timedelta(days=1)
        print(f"Last sync: {last}")
    else:
        start = date.today() - timedelta(days=args.days)
        print(f"No previous sync, looking back {args.days} days")

    if start > end:
        print(f"Already up to date (synced through {last}, DoltHub has through {end})")
        return

    print(f"{'='*60}\nINCREMENTAL SYNC: {start} → {end}\n{'='*60}")
    dates = trading_dates(start, end)
    print(f"{len(dates)} trading days to sync\n")

    root = parquet_root()
    root.mkdir(parents=True, exist_ok=True)
    total = sync_dates(dates, root)

    save_meta({
        "last_sync_date": end.isoformat(),
        "last_sync_time": datetime.now().isoformat(),
        "total_rows_synced": meta.get("total_rows_synced", 0) + total,
        "mode": "incremental",
    })
    print(f"\n✅ Incremental: {total:,} new rows written")


def cmd_single(args):
    """Sync a single date."""
    target = date.fromisoformat(args.date)
    print(f"{'='*60}\nSINGLE DATE: {target}\n{'='*60}")

    root = parquet_root()
    root.mkdir(parents=True, exist_ok=True)
    total = sync_dates([target], root, skip_existing=False)
    print(f"\n✅ {total:,} rows written for {target}")


def cmd_dry_run(args):
    """Show sync status without fetching."""
    meta = load_meta()
    remote_max = latest_dolthub_date()
    last = meta.get("last_sync_date", "never")

    print(f"DoltHub latest date: {remote_max}")
    print(f"Last local sync:     {last}")
    print(f"Total rows synced:   {meta.get('total_rows_synced', 0):,}")

    if last != "never":
        start = date.fromisoformat(last) + timedelta(days=1)
        pending = len(trading_dates(start, remote_max))
        print(f"Pending days:        {pending}")
    else:
        print("Pending days:        unknown (no sync yet)")

    # Quick sample to verify API works
    rows = query(f"SELECT * FROM option_chain WHERE date = '{remote_max}' AND act_symbol = 'AAPL' LIMIT 3")
    print(f"\nSample ({remote_max}, AAPL): {len(rows)} rows returned")
    if rows:
        print(f"  First row: {rows[0]}")


# ---------------------------------------------------------------------------
# Earnings calendar sync
# ---------------------------------------------------------------------------
def sync_earnings():
    """Download the full earnings_calendar table from DoltHub → Parquet.

    The table is small (~113K rows) so we fetch it all in paginated batches
    and write a single Parquet file.
    """
    print(f"{'='*60}\nEARNINGS CALENDAR SYNC\n{'='*60}")

    all_rows: list[dict] = []
    offset = 0
    while True:
        sql = f"SELECT act_symbol, date, `when` FROM earnings_calendar ORDER BY date, act_symbol LIMIT {ROW_LIMIT} OFFSET {offset}"
        rows = query(sql, EARNINGS_API)
        if not rows:
            break
        all_rows.extend(rows)
        print(f"  Fetched {len(all_rows):,} rows ...", end="\r", flush=True)
        if len(rows) < ROW_LIMIT:
            break
        offset += ROW_LIMIT
        time.sleep(API_DELAY)

    print(f"  Fetched {len(all_rows):,} rows total")

    if not all_rows:
        print("⚠ No earnings data returned")
        return

    df = pd.DataFrame(all_rows)
    df.rename(columns={"when": "timing"}, inplace=True)  # 'when' is a reserved word
    df["date"] = pd.to_datetime(df["date"]).dt.date

    # Normalize timing values
    timing_map = {
        "After market close": "amc",
        "Before market open": "bmo",
    }
    df["timing"] = df["timing"].map(timing_map).fillna("unknown")

    # Merge with existing CSV so we don't clobber Finnhub-overlaid columns
    # (eps_actual, eps_estimate, revenue_*, fiscal_year, fiscal_q, source).
    # DoltHub is the timing + universe baseline; Finnhub overlay adds the
    # fundamentals. If we overwrite, every nightly CI run wipes the actuals
    # we just spent calls fetching and the historical chart never deepens.
    #
    # how="outer" preserves rows that exist only in the prior CSV — typically
    # near-term earnings Finnhub posted ahead of DoltHub. We filter those
    # Finnhub-only rows to tickers that DoltHub HAS at some point carried,
    # so foreign-only tickers (.HK/.KS/.TW/etc.) and other Finnhub-only
    # symbols without forecast coverage don't leak into the universe.
    enriched_cols = [
        "fiscal_year",
        "fiscal_q",
        "eps_actual",
        "eps_estimate",
        "revenue_actual",
        "revenue_estimate",
        "source",
    ]
    csv_path = data_dir() / "earnings_calendar.csv"
    if csv_path.exists():
        try:
            existing = pd.read_csv(csv_path, keep_default_na=False)
            existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.date
            existing = existing.dropna(subset=["date", "act_symbol"])

            # Universe gate: keep existing rows whose ticker is EITHER in
            # tonight's fresh DoltHub snapshot OR has a current symbol JSON
            # (= the build pipeline successfully generated a forecast for
            # it in the last run). Anything else is dead weight — DoltHub
            # has stopped tracking it AND the frontend has no forecast to
            # show. The two legs handle the two cases we care about:
            #   - Fresh DoltHub snapshot: admits new tickers (1-cycle
            #     grace period so the build can generate a forecast).
            #   - Forecast set: preserves established coverage even when
            #     DoltHub temporarily drops a ticker from its forward-
            #     looking earnings projection between "reported" and
            #     "next estimate published".
            # Foreign-only Finnhub tickers stay filtered (never in DoltHub
            # snapshot, never get a forecast).
            forecast_tickers = {p.stem for p in SYMBOLS_DIR.glob("*.json")}
            # Subtract confirmed-delisted tickers so a stale forecast JSON
            # (or a lagging DoltHub snapshot) can't resurrect a name we've
            # deliberately removed (config/delisted_tickers.json).
            dolthub_universe = (
                (set(df["act_symbol"].unique()) | forecast_tickers) - delisted_tickers()
            )
            kept_before = len(existing)
            existing = existing[existing["act_symbol"].isin(dolthub_universe)]
            print(
                f"  Universe gate: forecast set {len(forecast_tickers):,} ∪ "
                f"fresh DoltHub {df['act_symbol'].nunique():,} → "
                f"kept {len(existing):,} of {kept_before:,} existing rows"
            )

            kept = [c for c in enriched_cols if c in existing.columns]
            merge_subset = ["act_symbol", "date", *kept]
            if "timing" in existing.columns:
                merge_subset.append("timing")
                existing = existing.rename(columns={"timing": "timing_prior"})
                merge_subset[-1] = "timing_prior"

            df = df.merge(
                existing[merge_subset],
                on=["act_symbol", "date"],
                how="outer",
            )

            # For Finnhub-only rows (no DoltHub side), df.timing is NaN — fall
            # back to the prior CSV's timing. For matched rows, DoltHub wins
            # unless DoltHub itself is 'unknown', in which case we preserve
            # any non-unknown timing we already had (e.g., Finnhub overlay,
            # inference, or CSV-level propagation).
            if "timing_prior" in df.columns:
                prior = df["timing_prior"].fillna("")
                new = df["timing"].fillna("")
                df["timing"] = new.where(
                    (new != "") & (new != "unknown"),
                    prior.where(prior != "", "unknown"),
                )
                df = df.drop(columns=["timing_prior"])

            # Rows from the fresh DoltHub pull that aren't yet in existing
            # have source=NaN after the outer merge. They came from DoltHub
            # by construction, so label them as such — otherwise they'd
            # write as empty-string source (e.g. the 194 mystery rows in
            # the failed 2026-05-19 run).
            if "source" in df.columns:
                df["source"] = df["source"].fillna("").astype(str)
                df.loc[df["source"].eq(""), "source"] = "dolthub"

            print(f"  Preserved {len(kept)} enriched column(s) from existing CSV: {kept}")
            print(f"  Merge: {len(df):,} total rows (DoltHub fresh + Finnhub-only carried forward)")
        except Exception as exc:
            print(f"  ⚠ Could not merge existing enriched columns: {exc}")

    # Apply ticker renames (old -> new) so earnings history carries over under
    # the company's current symbol. Only the renamed rows are rewritten; a
    # collision with a fresh DoltHub row already under the new symbol is then
    # deduped on (symbol, date).
    renames = ticker_renames()
    if renames:
        mask = df["act_symbol"].astype(str).str.strip().str.upper().isin(renames)
        if mask.any():
            df.loc[mask, "act_symbol"] = df.loc[mask, "act_symbol"].map(canonical_ticker)
            before = len(df)
            df = df.drop_duplicates(subset=["act_symbol", "date"], keep="last").reset_index(drop=True)
            print(
                f"  Renamed {int(mask.sum())} row(s) to current symbols "
                f"{sorted(set(renames.values()))}; deduped {before - len(df)} collision(s)"
            )

    # Final universe filter: drop delisted tickers that survived the merge.
    # The universe gate above only screens carried-forward rows; fresh DoltHub
    # rows reach here unfiltered, so a just-delisted name still in DoltHub's
    # snapshot would otherwise slip through.
    if delisted_tickers():
        before = len(df)
        df = df[~df["act_symbol"].map(is_delisted)].reset_index(drop=True)
        if len(df) != before:
            print(
                f"  Dropped {before - len(df):,} delisted-ticker row(s): "
                f"{sorted(delisted_tickers())}"
            )

    # Write Parquet
    out_path = data_dir() / "earnings_calendar.parquet"
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")
    print(f"  Written to {out_path}")

    df.to_csv(csv_path, index=False)
    print(f"  Written to {csv_path}")

    print(f"\n✅ Earnings sync complete: {len(df):,} rows")
    print(f"   Date range: {df['date'].min()} → {df['date'].max()}")
    print(f"   Symbols: {df['act_symbol'].nunique():,}")


# ---------------------------------------------------------------------------
# OHLCV sync (post-no-preference/stocks)
# ---------------------------------------------------------------------------
OHLCV_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("act_symbol", pa.string()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.int64()),
])


def sync_ohlcv(start_date_str: Optional[str] = None, end_date_str: Optional[str] = None,
               days: int = 30, full: bool = False):
    """Sync OHLCV stock price data from DoltHub → Parquet.

    The ohlcv table is keyed on (act_symbol, date) and moderate in size.
    We paginate by date, fetching all symbols per date in batches of 1000.
    """
    print(f"{'='*60}\nOHLCV SYNC\n{'='*60}")

    ohlcv_root = data_dir() / "parquet" / "ohlcv"
    ohlcv_root.mkdir(parents=True, exist_ok=True)
    meta = load_meta()

    # Determine date range
    if full:
        rows_min = query("SELECT date FROM ohlcv ORDER BY date ASC LIMIT 1", STOCKS_API)
        rows_max = query("SELECT date FROM ohlcv ORDER BY date DESC LIMIT 1", STOCKS_API)
        start = date.fromisoformat(start_date_str) if start_date_str else date.fromisoformat(rows_min[0]["date"])
        end = date.fromisoformat(end_date_str) if end_date_str else date.fromisoformat(rows_max[0]["date"])
    else:
        rows_max = query("SELECT date FROM ohlcv ORDER BY date DESC LIMIT 1", STOCKS_API)
        end = date.fromisoformat(rows_max[0]["date"])
        last_ohlcv = meta.get("last_ohlcv_date")
        if last_ohlcv:
            start = date.fromisoformat(last_ohlcv) + timedelta(days=1)
        else:
            start = end - timedelta(days=days)

    if start > end:
        print(f"Already up to date (synced through {meta.get('last_ohlcv_date')})")
        return

    dates = trading_dates(start, end)
    print(f"Syncing {len(dates)} trading days: {start} → {end}\n")

    total = 0
    for i, d in enumerate(dates):
        ds = d.isoformat()
        out_dir = ohlcv_root / f"year={d.year}" / f"month={d.month:02d}"
        out_path = out_dir / f"{ds}.parquet"
        if out_path.exists():
            continue

        # Fetch all rows for this date (paginate if >1000 symbols)
        all_rows: list[dict] = []
        last_sym = ""
        while True:
            sql = (
                f"SELECT date, act_symbol, `open`, high, low, `close`, volume "
                f"FROM ohlcv WHERE date = '{ds}' AND act_symbol > '{last_sym}' "
                f"ORDER BY act_symbol LIMIT {ROW_LIMIT}"
            )
            rows = query(sql, STOCKS_API)
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < ROW_LIMIT:
                break
            last_sym = rows[-1]["act_symbol"]
            time.sleep(API_DELAY)

        if not all_rows:
            continue

        df = pd.DataFrame(all_rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        for c in ["open", "high", "low", "close"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

        out_dir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, schema=OHLCV_SCHEMA, preserve_index=False)
        pq.write_table(table, out_path, compression="snappy")
        total += len(df)
        print(f"  [{i+1}/{len(dates)}] {ds} — {len(df):,} rows", flush=True)
        time.sleep(API_DELAY)

    meta["last_ohlcv_date"] = end.isoformat()
    meta["last_ohlcv_sync_time"] = datetime.now().isoformat()
    save_meta(meta)
    print(f"\n✅ OHLCV sync complete: {total:,} rows written to {ohlcv_root}")


# ---------------------------------------------------------------------------
# Volatility history sync (post-no-preference/options/volatility_history)
# ---------------------------------------------------------------------------
# Moderate-size table: one row per (act_symbol, date). Lives in the *options*
# repo, not stocks. Paginated per-date via keyset on act_symbol.
VOLHIST_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("act_symbol", pa.string()),
    ("hv_current", pa.float64()),
    ("hv_week_ago", pa.float64()),
    ("hv_month_ago", pa.float64()),
    ("hv_year_high", pa.float64()),
    ("hv_year_high_date", pa.date32()),
    ("hv_year_low", pa.float64()),
    ("hv_year_low_date", pa.date32()),
    ("iv_current", pa.float64()),
    ("iv_week_ago", pa.float64()),
    ("iv_month_ago", pa.float64()),
    ("iv_year_high", pa.float64()),
    ("iv_year_high_date", pa.date32()),
    ("iv_year_low", pa.float64()),
    ("iv_year_low_date", pa.date32()),
])


def sync_volhist(start_date_str: Optional[str] = None, end_date_str: Optional[str] = None,
                 days: int = 30, full: bool = False):
    """Sync volatility_history from DoltHub → Parquet.

    Keyed on (act_symbol, date). One row per symbol per day containing current
    HV/IV plus week/month/year-ago snapshots. Used downstream to compute
    IV Rank and vol-momentum features.
    """
    print(f"{'='*60}\nVOLATILITY HISTORY SYNC\n{'='*60}")

    volhist_root = data_dir() / "parquet" / "volatility_history"
    volhist_root.mkdir(parents=True, exist_ok=True)
    meta = load_meta()

    if full:
        rows_min = query("SELECT date FROM volatility_history ORDER BY date ASC LIMIT 1", OPTIONS_API)
        rows_max = query("SELECT date FROM volatility_history ORDER BY date DESC LIMIT 1", OPTIONS_API)
        start = date.fromisoformat(start_date_str) if start_date_str else date.fromisoformat(rows_min[0]["date"])
        end = date.fromisoformat(end_date_str) if end_date_str else date.fromisoformat(rows_max[0]["date"])
    else:
        rows_max = query("SELECT date FROM volatility_history ORDER BY date DESC LIMIT 1", OPTIONS_API)
        end = date.fromisoformat(rows_max[0]["date"])
        last = meta.get("last_volhist_date")
        if last:
            start = date.fromisoformat(last) + timedelta(days=1)
        else:
            start = end - timedelta(days=days)

    if start > end:
        print(f"Already up to date (synced through {meta.get('last_volhist_date')})")
        return

    dates = trading_dates(start, end)
    print(f"Syncing {len(dates)} trading days: {start} → {end}\n")

    cols = (
        "date, act_symbol, hv_current, hv_week_ago, hv_month_ago, "
        "hv_year_high, hv_year_high_date, hv_year_low, hv_year_low_date, "
        "iv_current, iv_week_ago, iv_month_ago, "
        "iv_year_high, iv_year_high_date, iv_year_low, iv_year_low_date"
    )

    total = 0
    for i, d in enumerate(dates):
        ds = d.isoformat()
        out_dir = volhist_root / f"year={d.year}" / f"month={d.month:02d}"
        out_path = out_dir / f"{ds}.parquet"
        if out_path.exists():
            continue

        all_rows: list[dict] = []
        last_sym = ""
        while True:
            sql = (
                f"SELECT {cols} FROM volatility_history "
                f"WHERE date = '{ds}' AND act_symbol > '{last_sym}' "
                f"ORDER BY act_symbol LIMIT {ROW_LIMIT}"
            )
            rows = query(sql, OPTIONS_API)
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < ROW_LIMIT:
                break
            last_sym = rows[-1]["act_symbol"]
            time.sleep(API_DELAY)

        if not all_rows:
            continue

        df = pd.DataFrame(all_rows)
        # Coerce types
        for c in ["hv_current", "hv_week_ago", "hv_month_ago", "hv_year_high", "hv_year_low",
                  "iv_current", "iv_week_ago", "iv_month_ago", "iv_year_high", "iv_year_low"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        for c in ["date", "hv_year_high_date", "hv_year_low_date",
                  "iv_year_high_date", "iv_year_low_date"]:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date

        out_dir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, schema=VOLHIST_SCHEMA, preserve_index=False)
        pq.write_table(table, out_path, compression="snappy")
        total += len(df)
        print(f"  [{i+1}/{len(dates)}] {ds} — {len(df):,} rows", flush=True)
        time.sleep(API_DELAY)

    meta["last_volhist_date"] = end.isoformat()
    meta["last_volhist_sync_time"] = datetime.now().isoformat()
    save_meta(meta)
    print(f"\n✅ Volatility history sync complete: {total:,} rows written to {volhist_root}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Sync data from DoltHub → local Parquet")

    parser.add_argument("--days", type=int, default=3, help="Lookback days for incremental (default 3)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full", action="store_true", help="Full date-range sync")
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--date", type=str, default=None, help="Sync a single date")
    parser.add_argument("--force", action="store_true", help="Re-download even if Parquet exists")
    parser.add_argument("--earnings", action="store_true", help="Sync earnings calendar")
    parser.add_argument("--ohlcv", action="store_true", help="Sync OHLCV stock prices")
    parser.add_argument("--volhist", action="store_true",
                        help="Sync volatility history (HV/IV current + week/month/year snapshots)")

    args = parser.parse_args()

    if args.earnings:
        sync_earnings()
        return

    if args.ohlcv:
        sync_ohlcv(args.start_date, args.end_date, args.days, args.full)
        return

    if args.volhist:
        sync_volhist(args.start_date, args.end_date, args.days, args.full)
        return

    print(f"Parquet root: {parquet_root()}\n")

    if args.dry_run:
        cmd_dry_run(args)
    elif args.date:
        cmd_single(args)
    elif args.full:
        cmd_full(args)
    else:
        cmd_incremental(args)


if __name__ == "__main__":
    main()
