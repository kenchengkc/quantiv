#!/usr/bin/env python3
"""
Sync VIX (CBOE Volatility Index) daily close → Parquet.

Source: CBOE's official VIX history CSV, served from its CDN (no API key):
  https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
Columns: DATE,OPEN,HIGH,LOW,CLOSE (full history from 1990, ~9k rows).

Previously pulled from FRED's fredgraph.csv graph endpoint, which is meant for
the web UI and intermittently read-times-out from CI (it killed the whole daily
refresh). The CBOE CDN responds in <200ms and is the authoritative source.

We always pull the full history and overwrite — keeps the file authoritative
without incremental bookkeeping.

Output: data/parquet/vix/vix.parquet  (single file, columns: date, vix_close)

Usage:
  .venv/bin/python3 scripts/sync_vix.py
"""

import os
import sys
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
# Some CDNs 403 a bare urllib/requests UA; send a browser-like one.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; quantiv-vix-sync/1.0)"}


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def fetch_csv(url: str, attempts: int = 4) -> str:
    """GET a CSV with explicit connect/read timeouts + backoff, so a transient
    blip is retried instead of being fatal to the whole daily refresh."""
    delays = [2, 5, 15]
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            res = requests.get(url, headers=HEADERS, timeout=(10, 30))  # (connect, read)
            res.raise_for_status()
            return res.text
        except requests.RequestException as exc:
            last_exc = exc
            if i < attempts - 1:
                wait = delays[min(i, len(delays) - 1)]
                print(
                    f"⚠️  fetch attempt {i + 1}/{attempts} failed "
                    f"({str(exc)[:120]}); retrying in {wait}s",
                    flush=True,
                )
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def main() -> None:
    out_dir = data_dir() / "parquet" / "vix"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "vix.parquet"

    print(f"📥 fetching {VIX_URL}", flush=True)
    csv_text = fetch_csv(VIX_URL)

    # CBOE CSV columns: DATE,OPEN,HIGH,LOW,CLOSE (DATE is MM/DD/YYYY).
    df = pd.read_csv(StringIO(csv_text))
    df.columns = [c.strip().upper() for c in df.columns]
    if "DATE" not in df.columns or "CLOSE" not in df.columns:
        print(f"❌ unexpected columns: {df.columns.tolist()}")
        sys.exit(1)

    df = df.rename(columns={"DATE": "date", "CLOSE": "vix_close"})[["date", "vix_close"]]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["vix_close"] = pd.to_numeric(df["vix_close"], errors="coerce")
    df = df.dropna(subset=["vix_close"]).sort_values("date").reset_index(drop=True)

    schema = pa.schema([
        ("date", pa.date32()),
        ("vix_close", pa.float64()),
    ])
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")

    print(f"✅ wrote {len(df):,} rows → {out_path}")
    print(f"   range: {df['date'].min()} → {df['date'].max()}")
    print(f"   latest VIX: {df.iloc[-1]['vix_close']:.2f}")


if __name__ == "__main__":
    main()
