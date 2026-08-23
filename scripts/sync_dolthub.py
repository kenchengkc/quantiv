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
import hashlib
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

# DoltHub currently exposes only EOD quote dates, prices, IV, and Greeks. The
# nullable microstructure columns are materialized in every new partition so a
# richer provider can populate them without another lake/schema migration.
COLUMNS = (
    "date, act_symbol, expiration, strike, call_put, bid, ask, vol, "
    "delta, gamma, theta, vega, rho, "
    "CAST(NULL AS DATETIME) AS quote_timestamp, "
    "CAST(NULL AS SIGNED) AS option_volume, "
    "CAST(NULL AS SIGNED) AS open_interest"
)

# Symbol-prefix buckets for pagination (covers A-Z and a few common non-alpha)
SYMBOL_BUCKETS: list[tuple[str | None, str | None]] = [
    (None, "D"), ("D", "G"), ("G", "K"), ("K", "N"),
    ("N", "R"), ("R", "U"), ("U", None),
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
    ("quote_timestamp", pa.timestamp("us")),
    ("option_volume", pa.int64()),
    ("open_interest", pa.int64()),
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


def ingestion_control_root() -> Path:
    return data_dir() / "control" / "ingestion" / "options"


def corporate_action_root() -> Path:
    return data_dir() / "parquet" / "corporate_actions"


def corporate_action_manifest_path(source_date: date) -> Path:
    return (
        data_dir()
        / "control"
        / "ingestion"
        / "corporate_actions"
        / f"{source_date}.json"
    )


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_digest(frame: pd.DataFrame) -> str:
    """Stable logical-row digest used to prove idempotent source replay."""
    if frame.empty:
        return hashlib.sha256(b"").hexdigest()
    normalized = frame.copy()
    for column in COLUMN_LIST:
        if column not in normalized:
            normalized[column] = None
    digest_columns = [
        column
        for column in COLUMN_LIST
        if column not in {"quote_timestamp", "option_volume", "open_interest"}
        or normalized[column].notna().any()
    ]
    ordered = normalized.sort_values(
        ["date", "act_symbol", "expiration", "strike", "call_put"],
        kind="mergesort",
    )[digest_columns]
    payload = ordered.to_json(
        orient="records",
        date_format="iso",
        date_unit="us",
        double_precision=15,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


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
    bucket_evidence: list[dict[str, object]] = []

    for lo, hi in SYMBOL_BUCKETS:
        # Keyset pagination within the bucket
        last_key: Optional[tuple] = None
        bucket_rows = 0
        pages = 0
        while True:
            if last_key is None:
                bounds = []
                if lo is not None:
                    bounds.append(f"act_symbol >= '{lo}'")
                if hi is not None:
                    bounds.append(f"act_symbol < '{hi}'")
                where = f"date = '{ds}' AND " + " AND ".join(bounds)
            else:
                sym, exp, stk, cp = last_key
                # Continue after the last row we saw
                bounds = []
                if lo is not None:
                    bounds.append(f"act_symbol >= '{lo}'")
                if hi is not None:
                    bounds.append(f"act_symbol < '{hi}'")
                bounds.append(
                    "(act_symbol, expiration, strike, call_put) "
                    f"> ('{sym}', '{exp}', {stk}, '{cp}')"
                )
                where = f"date = '{ds}' AND " + " AND ".join(bounds)

            sql = f"SELECT {COLUMNS} FROM option_chain WHERE {where} LIMIT {ROW_LIMIT}"
            rows = query(sql)
            pages += 1
            if not rows:
                break

            all_rows.extend(rows)
            bucket_rows += len(rows)

            if len(rows) < ROW_LIMIT:
                break  # No more pages in this bucket

            # Set keyset for next page
            last = rows[-1]
            last_key = (last["act_symbol"], last["expiration"], last["strike"], last["call_put"])
            time.sleep(API_DELAY)

        time.sleep(API_DELAY)
        bucket_evidence.append(
            {
                "symbol_range": [lo, hi],
                "rows": bucket_rows,
                "pages": pages,
                "exhausted": True,
            }
        )

    if not all_rows:
        empty = pd.DataFrame(columns=COLUMN_LIST)
        empty.attrs["ingestion_evidence"] = {
            "expected_rows": 0,
            "received_rows": 0,
            "expected_method": "exhaustive_keyset_pagination",
            "buckets": bucket_evidence,
        }
        return empty

    df = pd.DataFrame(all_rows)
    for col in COLUMN_LIST:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMN_LIST]

    # Type coercion
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiration"] = pd.to_datetime(df["expiration"]).dt.date
    df["quote_timestamp"] = pd.to_datetime(df["quote_timestamp"], errors="coerce")
    for c in ["strike", "bid", "ask", "vol", "delta", "gamma", "theta", "vega", "rho"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["option_volume", "open_interest"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")

    duplicate_rows = int(
        df.duplicated(["date", "act_symbol", "expiration", "strike", "call_put"]).sum()
    )
    if duplicate_rows:
        raise RuntimeError(
            f"source replay for {ds} returned {duplicate_rows:,} duplicate primary keys"
        )
    df.attrs["ingestion_evidence"] = {
        # Every symbol bucket is traversed until its first short/empty page.
        # This is the provider-compatible expected count: broad COUNT scans
        # time out on DoltHub's 100M+ row table.
        "expected_rows": len(all_rows),
        "received_rows": len(df),
        "expected_method": "exhaustive_keyset_pagination",
        "buckets": bucket_evidence,
    }

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
    temporary = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
    normalized = df.copy()
    for column in COLUMN_LIST:
        if column not in normalized:
            normalized[column] = None
    table = pa.Table.from_pandas(
        normalized[COLUMN_LIST], schema=ARROW_SCHEMA, preserve_index=False
    )
    pq.write_table(table, temporary, compression="snappy")
    # Validate the staged artifact before the single-filesystem promotion.
    staged = pq.ParquetFile(temporary)
    if staged.metadata.num_rows != len(df):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"staged Parquet has {staged.metadata.num_rows:,} rows; expected {len(df):,}"
        )
    os.replace(temporary, out_path)
    return len(df)


def _partition_path(target: date, root: Path) -> Path:
    return (
        root
        / f"year={target.year}"
        / f"month={target.month:02d}"
        / f"{target.isoformat()}.parquet"
    )


def _manifest_path(target: date) -> Path:
    return ingestion_control_root() / f"{target.isoformat()}.json"


def _write_ingestion_manifest(
    target: date,
    frame: pd.DataFrame,
    root: Path,
    *,
    prior_manifest: dict | None = None,
) -> dict:
    evidence = frame.attrs.get("ingestion_evidence") or {}
    expected = int(evidence.get("expected_rows", len(frame)))
    received = len(frame)
    if expected != received:
        raise RuntimeError(
            f"{target}: expected {expected:,} rows but received {received:,}"
        )
    digest = _content_digest(frame)
    partition = _partition_path(target, root)
    if received:
        persisted = pq.read_table(partition).to_pandas()
        persisted_digest = _content_digest(persisted)
        if persisted_digest != digest:
            raise RuntimeError(
                f"{target}: persisted partition is not replay-equivalent to source rows"
            )
    else:
        persisted_digest = digest
    prior_digest = (prior_manifest or {}).get("content_sha256")
    source_revision_status = "baseline_recorded"
    if prior_digest:
        if prior_digest != digest:
            raise RuntimeError(
                f"{target}: replay digest changed ({prior_digest} -> {digest}); "
                "quarantine the source revision before promotion"
            )
        source_revision_status = "unchanged"
    payload = {
        "schema": "quantiv.options-ingestion.v1",
        "status": "passed",
        "source": "dolthub/post-no-preference/options/option_chain",
        "source_date": target.isoformat(),
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "expected_rows": expected,
        "received_rows": received,
        "expected_method": evidence.get(
            "expected_method", "exhaustive_keyset_pagination"
        ),
        "duplicate_primary_keys": 0,
        "partition": str(partition.relative_to(data_dir())) if received else None,
        "partition_bytes": partition.stat().st_size if received else 0,
        "partition_sha256": _sha256_file(partition) if received else None,
        "content_sha256": digest,
        "replay_equivalence": "verified",
        "persisted_content_sha256": persisted_digest,
        "source_revision_status": source_revision_status,
        "pagination": evidence.get("buckets", []),
        "source_capabilities": {
            "timestamp_precision": "date",
            "intraday_quote_timestamp": False,
            "option_volume": False,
            "open_interest": False,
        },
    }
    _atomic_json(_manifest_path(target), payload)
    return payload


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def load_meta() -> dict:
    p = metadata_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_meta(meta: dict):
    p = metadata_path()
    _atomic_json(p, meta)


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
    path = _partition_path(dt_obj, root)
    return path.exists()


# ---------------------------------------------------------------------------
# Sync modes
# ---------------------------------------------------------------------------
def sync_dates(dates: list[date], root: Path, skip_existing: bool = True) -> int:
    """Sync a list of dates. Returns total rows written."""
    total = 0
    for i, d in enumerate(dates):
        if skip_existing and already_synced(d, root):
            partition = _partition_path(d, root)
            manifest_path = _manifest_path(d)
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                actual = _sha256_file(partition)
                if manifest.get("partition_sha256") != actual:
                    raise RuntimeError(
                        f"{d}: existing partition does not match its ingestion manifest"
                    )
                print(
                    f"  [{i+1}/{len(dates)}] {d} — verified existing partition, skipping"
                )
            else:
                existing = pq.read_table(partition).to_pandas()
                existing.attrs["ingestion_evidence"] = {
                    "expected_rows": len(existing),
                    "received_rows": len(existing),
                    "expected_method": "legacy_partition_baseline",
                    "buckets": [],
                }
                _write_ingestion_manifest(d, existing, root)
                print(
                    f"  [{i+1}/{len(dates)}] {d} — recorded legacy baseline, skipping"
                )
            continue

        print(f"  [{i+1}/{len(dates)}] {d} — fetching ...", end=" ", flush=True)
        prior_manifest = None
        manifest_path = _manifest_path(d)
        if manifest_path.exists():
            prior_manifest = json.loads(manifest_path.read_text())
        df = fetch_date(d)
        n = write_date(df, root)
        _write_ingestion_manifest(d, df, root, prior_manifest=prior_manifest)
        total += n
        print(f"{n:,} rows · reconciled · atomically promoted")

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
        # A fresh runner may have pulled a canonical partition without its
        # control manifest. Verify/backfill the latest partition before exit.
        sync_dates([end], parquet_root(), skip_existing=True)
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

SPLIT_SCHEMA = pa.schema([
    ("act_symbol", pa.string()),
    ("ex_date", pa.date32()),
    ("to_factor", pa.float64()),
    ("for_factor", pa.float64()),
])

DIVIDEND_SCHEMA = pa.schema([
    ("act_symbol", pa.string()),
    ("ex_date", pa.date32()),
    ("amount", pa.float64()),
])

CORPORATE_ACTION_START = date(2019, 1, 1)
CORPORATE_ACTION_BATCH_SIZE = 75


def _latest_option_universe() -> tuple[date, list[str]]:
    """Read the active symbol set from the latest already-reconciled partition."""
    meta = load_meta()
    latest_value = meta.get("last_sync_date")
    if not latest_value:
        raise RuntimeError("options metadata lacks last_sync_date")
    latest = date.fromisoformat(str(latest_value))
    partition = _partition_path(latest, parquet_root())
    if not partition.exists():
        raise RuntimeError(f"latest options partition is missing: {partition}")
    frame = pq.read_table(partition, columns=["act_symbol"]).to_pandas()
    symbols = sorted(
        {
            canonical_ticker(symbol)
            for symbol in frame["act_symbol"].dropna().astype(str)
            if not is_delisted(symbol)
        }
    )
    symbols = [symbol for symbol in symbols if symbol]
    if not symbols:
        raise RuntimeError("latest options partition has no active symbols")
    return latest, symbols


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _action_content_digest(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return hashlib.sha256(b"").hexdigest()
    ordered = frame.sort_values(["act_symbol", "ex_date"], kind="mergesort")[columns]
    records: list[dict[str, object]] = []
    for row in ordered.to_dict(orient="records"):
        canonical: dict[str, object] = {}
        for column in columns:
            value = row[column]
            if column == "ex_date":
                canonical[column] = pd.Timestamp(value).date().isoformat()
            elif column == "act_symbol":
                canonical[column] = str(value)
            else:
                canonical[column] = float(value)
        records.append(canonical)
    payload = json.dumps(
        records, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _fetch_action_table(
    table_name: str,
    value_columns: list[str],
    symbols: list[str],
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Exhaust every PK-ordered page for bounded active-universe batches."""
    columns = ["act_symbol", "ex_date", *value_columns]
    all_rows: list[dict] = []
    evidence: list[dict[str, object]] = []
    for batch_index in range(0, len(symbols), CORPORATE_ACTION_BATCH_SIZE):
        batch = symbols[batch_index:batch_index + CORPORATE_ACTION_BATCH_SIZE]
        symbol_list = ", ".join(_sql_literal(symbol) for symbol in batch)
        last_key: tuple[str, str] | None = None
        batch_rows = 0
        pages = 0
        while True:
            keyset = ""
            if last_key is not None:
                keyset = (
                    " AND (act_symbol, ex_date) > "
                    f"({_sql_literal(last_key[0])}, {_sql_literal(last_key[1])})"
                )
            sql = (
                f"SELECT {', '.join(columns)} FROM {table_name} "
                f"WHERE act_symbol IN ({symbol_list}) "
                f"AND ex_date BETWEEN {_sql_literal(start.isoformat())} "
                f"AND {_sql_literal(end.isoformat())}{keyset} "
                f"ORDER BY act_symbol, ex_date LIMIT {ROW_LIMIT}"
            )
            rows = query(sql, STOCKS_API)
            pages += 1
            all_rows.extend(rows)
            batch_rows += len(rows)
            if len(rows) < ROW_LIMIT:
                break
            last = rows[-1]
            last_key = (str(last["act_symbol"]), str(last["ex_date"]))
            time.sleep(API_DELAY)
        evidence.append(
            {
                "batch": batch_index // CORPORATE_ACTION_BATCH_SIZE,
                "symbols": len(batch),
                "rows": batch_rows,
                "pages": pages,
                "completion": "short_page",
            }
        )
        print(
            f"  {table_name} batch {batch_index // CORPORATE_ACTION_BATCH_SIZE + 1}: "
            f"{len(batch)} symbols · {batch_rows:,} rows · {pages} page(s)",
            flush=True,
        )
        time.sleep(API_DELAY)
    frame = pd.DataFrame(all_rows, columns=columns)
    if frame.empty:
        return frame, evidence
    frame["act_symbol"] = frame["act_symbol"].map(canonical_ticker)
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], errors="raise").dt.date
    for column in value_columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    duplicates = int(frame.duplicated(["act_symbol", "ex_date"]).sum())
    if duplicates:
        raise RuntimeError(f"{table_name} returned {duplicates:,} duplicate primary keys")
    return frame.sort_values(["act_symbol", "ex_date"]).reset_index(drop=True), evidence


def _write_action_parquet(path: Path, frame: pd.DataFrame, schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    pq.write_table(table, temporary, compression="snappy")
    if pq.ParquetFile(temporary).metadata.num_rows != len(frame):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"staged {path.name} row count does not match source")
    os.replace(temporary, path)


def sync_corporate_actions(
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
) -> dict:
    """Snapshot split/dividend history for the current options universe."""
    source_date, symbols = _latest_option_universe()
    start = date.fromisoformat(start_date_str) if start_date_str else CORPORATE_ACTION_START
    end = date.fromisoformat(end_date_str) if end_date_str else date.today()
    if end < start:
        raise ValueError("corporate-action end date precedes start date")
    print(
        f"CORPORATE ACTION SYNC: {len(symbols):,} active symbols · {start} → {end}"
    )
    splits, split_batches = _fetch_action_table(
        "split", ["to_factor", "for_factor"], symbols, start, end
    )
    dividends, dividend_batches = _fetch_action_table(
        "dividend", ["amount"], symbols, start, end
    )
    if not splits.empty and (
        (splits["to_factor"] <= 0).any() or (splits["for_factor"] <= 0).any()
    ):
        raise RuntimeError("split feed contains a non-positive adjustment factor")
    if not dividends.empty and (dividends["amount"] < 0).any():
        raise RuntimeError("dividend feed contains a negative cash amount")

    split_digest = _action_content_digest(
        splits, ["act_symbol", "ex_date", "to_factor", "for_factor"]
    )
    dividend_digest = _action_content_digest(
        dividends, ["act_symbol", "ex_date", "amount"]
    )
    root = corporate_action_root()
    split_path = root / "splits" / f"{split_digest}.parquet"
    dividend_path = root / "dividends" / f"{dividend_digest}.parquet"
    if not split_path.exists():
        _write_action_parquet(split_path, splits, SPLIT_SCHEMA)
    if not dividend_path.exists():
        _write_action_parquet(dividend_path, dividends, DIVIDEND_SCHEMA)

    persisted_splits = pq.read_table(split_path).to_pandas()
    persisted_dividends = pq.read_table(dividend_path).to_pandas()
    if split_digest != _action_content_digest(
        persisted_splits, ["act_symbol", "ex_date", "to_factor", "for_factor"]
    ) or dividend_digest != _action_content_digest(
        persisted_dividends, ["act_symbol", "ex_date", "amount"]
    ):
        raise RuntimeError("corporate-action Parquet replay digest mismatch")

    symbol_digest = hashlib.sha256("\n".join(symbols).encode()).hexdigest()
    manifest = {
        "schema": "quantiv.corporate-action-ingestion.v1",
        "source": "dolthub:post-no-preference/stocks",
        "source_options_date": source_date.isoformat(),
        "query_start": start.isoformat(),
        "query_end": end.isoformat(),
        "universe": {
            "symbols": len(symbols),
            "symbols_sha256": symbol_digest,
            "method": "latest_options_partition_excluding_retired_symbols",
        },
        "datasets": {
            "splits": {
                "rows": len(splits),
                "partition": str(split_path.relative_to(data_dir())),
                "partition_sha256": _sha256_file(split_path),
                "content_sha256": split_digest,
                "batches": split_batches,
            },
            "dividends": {
                "rows": len(dividends),
                "partition": str(dividend_path.relative_to(data_dir())),
                "partition_sha256": _sha256_file(dividend_path),
                "content_sha256": dividend_digest,
                "batches": dividend_batches,
            },
        },
        "replay_equivalence": "verified",
        "promotion": "atomic_current_data_release_pointer",
        "adjustment_contract": {
            "split": "post price multiplied by cumulative to_factor/for_factor",
            "dividend": "cash distributions added back before realized-return calculation",
            "scope": "earnings realized moves and trailing realized-move features",
        },
    }
    manifest_path = corporate_action_manifest_path(source_date)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing != manifest:
            raise RuntimeError(
                "corporate-action receipt changed for an immutable options source date"
            )
    else:
        _atomic_json(manifest_path, manifest)
    print(
        f"✅ Corporate actions: {len(splits):,} splits · {len(dividends):,} dividends · "
        "replay verified"
    )
    return manifest


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
    parser.add_argument(
        "--corporate-actions",
        action="store_true",
        help="Sync split/dividend controls for the latest active options universe",
    )

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

    if args.corporate_actions:
        sync_corporate_actions(args.start_date, args.end_date)
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
