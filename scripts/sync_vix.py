#!/usr/bin/env python3
"""
Sync VIX (CBOE Volatility Index) daily close from FRED → Parquet.

Source: https://fred.stlouisfed.org/series/VIXCLS  (public CSV, no API key)

The FRED CSV is small (~35 years × 1 row/day ≈ 9k rows). We always pull
the full history and overwrite — keeps the file authoritative without
incremental bookkeeping.

Output: data/parquet/vix/vix.parquet  (single file, columns: date, vix_close)

Usage:
  .venv/bin/python3 scripts/sync_vix.py
"""

import os
import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def main() -> None:
    out_dir = data_dir() / "parquet" / "vix"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "vix.parquet"

    print(f"📥 fetching {FRED_URL}", flush=True)
    res = requests.get(FRED_URL, timeout=30)
    res.raise_for_status()

    # FRED CSV columns: observation_date, VIXCLS
    df = pd.read_csv(StringIO(res.text))
    df.columns = [c.strip() for c in df.columns]
    if "observation_date" not in df.columns or "VIXCLS" not in df.columns:
        print(f"❌ unexpected columns: {df.columns.tolist()}")
        sys.exit(1)

    df = df.rename(columns={"observation_date": "date", "VIXCLS": "vix_close"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["vix_close"] = pd.to_numeric(df["vix_close"], errors="coerce")
    df = df.dropna(subset=["vix_close"])

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
