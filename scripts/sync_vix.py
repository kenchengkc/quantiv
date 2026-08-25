#!/usr/bin/env python3
"""
Sync VIX (CBOE Volatility Index) daily close to Parquet.

Source: CBOE's official VIX history CSV, served from its CDN (no API key):
  https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
Columns: DATE,OPEN,HIGH,LOW,CLOSE (full history from 1990, ~9k rows).

Previously pulled from FRED's fredgraph.csv graph endpoint, which is meant for
the web UI and intermittently read-times-out from CI (it killed the whole daily
refresh). The CBOE CDN responds quickly and is the authoritative source.

The authoritative release object is content-addressed and therefore immutable:
  data/parquet/vix/vix-through-YYYY-MM-DD-<sha256>.parquet

For existing DuckDB/scoring consumers, vix.parquet is also written as a local
mutable alias. The release builder and R2 publisher deliberately exclude that
alias; R2 stores only immutable snapshot objects.

Usage:
  .venv/bin/python3 scripts/sync_vix.py
"""

from __future__ import annotations

import hashlib
from io import StringIO
import os
from pathlib import Path
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; quantiv-vix-sync/1.0)"}


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def fetch_csv(url: str, attempts: int = 4) -> str:
    """GET a CSV with explicit connect/read timeouts and bounded backoff."""
    delays = [2, 5, 15]
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            res = requests.get(url, headers=HEADERS, timeout=(10, 30))
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


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    schema = pa.schema(
        [
            ("date", pa.date32()),
            ("vix_close", pa.float64()),
        ]
    )
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="snappy")
    return sink.getvalue().to_pybytes()


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_snapshot(df: pd.DataFrame, out_dir: Path) -> Path:
    payload = _parquet_bytes(df)
    digest = hashlib.sha256(payload).hexdigest()
    latest_date = df.iloc[-1]["date"]
    snapshot = out_dir / f"vix-through-{latest_date.isoformat()}-{digest}.parquet"
    alias = out_dir / "vix.parquet"

    if snapshot.exists():
        if snapshot.read_bytes() != payload:
            raise RuntimeError(f"content-addressed VIX snapshot is corrupted: {snapshot}")
    else:
        _atomic_write(snapshot, payload)

    # Keep the old path only as a local compatibility alias. It is intentionally
    # excluded from immutable releases and R2 publication.
    _atomic_write(alias, payload)

    # R2 pull restores historical immutable snapshots. Keep exactly the active
    # snapshot locally before the next release manifest is built.
    for path in out_dir.glob("*.parquet"):
        if path not in {snapshot, alias}:
            path.unlink()

    return snapshot


def main() -> None:
    out_dir = data_dir() / "parquet" / "vix"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"📥 fetching {VIX_URL}", flush=True)
    csv_text = fetch_csv(VIX_URL)

    df = pd.read_csv(StringIO(csv_text))
    df.columns = [c.strip().upper() for c in df.columns]
    if "DATE" not in df.columns or "CLOSE" not in df.columns:
        print(f"❌ unexpected columns: {df.columns.tolist()}")
        sys.exit(1)

    df = df.rename(columns={"DATE": "date", "CLOSE": "vix_close"})[["date", "vix_close"]]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["vix_close"] = pd.to_numeric(df["vix_close"], errors="coerce")
    df = df.dropna(subset=["date", "vix_close"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        raise RuntimeError("CBOE VIX history contained no usable rows")
    if df["date"].duplicated().any():
        raise RuntimeError("CBOE VIX history contains duplicate dates")

    snapshot = _publish_snapshot(df, out_dir)

    print(f"✅ wrote {len(df):,} rows → {snapshot}")
    print(f"   range: {df['date'].min()} → {df['date'].max()}")
    print(f"   latest VIX: {df.iloc[-1]['vix_close']:.2f}")


if __name__ == "__main__":
    main()
