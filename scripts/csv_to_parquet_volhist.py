#!/usr/bin/env python3
"""
Convert volatility_history.csv to date-partitioned Parquet files.

Fixes the schema mismatch (TIMESTAMP WITH TIME ZONE vs TIMESTAMP_NS) in
existing Parquet files by regenerating everything from the CSV with
consistent types.

Output: data/parquet/volatility_history/year=YYYY/month=MM/*.parquet

Usage:
  python scripts/csv_to_parquet_volhist.py
"""

import os
import shutil
import time
from pathlib import Path

import duckdb


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def main():
    data_dir = get_data_dir()
    csv_path = data_dir / "volatility_history.csv"
    parquet_root = data_dir / "parquet" / "volatility_history"

    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        return

    print(f"CSV:          {csv_path} ({csv_path.stat().st_size / 1e6:.1f} MB)")
    print(f"Parquet root: {parquet_root}")

    # Remove old broken parquet files (mixed schemas)
    if parquet_root.exists():
        print("Removing old parquet files (broken schema)...")
        shutil.rmtree(parquet_root)

    parquet_root.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(":memory:")
    conn.execute("SET threads = 4")
    conn.execute("SET memory_limit = '4GB'")

    print("\nConverting (single-pass bulk write)...")
    t0 = time.time()

    conn.execute(f"""
        COPY (
            SELECT
                CAST(date AS DATE) AS date,
                act_symbol,
                CAST(hv_current AS DOUBLE) AS hv_current,
                CAST(hv_week_ago AS DOUBLE) AS hv_week_ago,
                CAST(hv_month_ago AS DOUBLE) AS hv_month_ago,
                CAST(hv_year_high AS DOUBLE) AS hv_year_high,
                TRY_CAST(hv_year_high_date AS DATE) AS hv_year_high_date,
                CAST(hv_year_low AS DOUBLE) AS hv_year_low,
                TRY_CAST(hv_year_low_date AS DATE) AS hv_year_low_date,
                CAST(iv_current AS DOUBLE) AS iv_current,
                CAST(iv_week_ago AS DOUBLE) AS iv_week_ago,
                CAST(iv_month_ago AS DOUBLE) AS iv_month_ago,
                CAST(iv_year_high AS DOUBLE) AS iv_year_high,
                TRY_CAST(iv_year_high_date AS DATE) AS iv_year_high_date,
                CAST(iv_year_low AS DOUBLE) AS iv_year_low,
                TRY_CAST(iv_year_low_date AS DATE) AS iv_year_low_date,
                YEAR(CAST(date AS DATE)) AS year,
                LPAD(CAST(MONTH(CAST(date AS DATE)) AS VARCHAR), 2, '0') AS month
            FROM read_csv_auto('{csv_path}')
        ) TO '{parquet_root}'
        (FORMAT PARQUET, COMPRESSION SNAPPY, PARTITION_BY (year, month), OVERWRITE_OR_IGNORE)
    """)

    elapsed = time.time() - t0
    print(f"Bulk write complete ({elapsed:.1f}s)")

    # Verify
    count = conn.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{parquet_root}/**/*.parquet', hive_partitioning=true)
    """).fetchone()[0]

    date_range = conn.execute(f"""
        SELECT MIN(date), MAX(date) 
        FROM read_parquet('{parquet_root}/**/*.parquet', hive_partitioning=true)
    """).fetchone()

    n_files = sum(1 for _ in parquet_root.rglob("*.parquet"))
    total_size = sum(f.stat().st_size for f in parquet_root.rglob("*.parquet"))

    print("\nConversion complete")
    print(f"   Rows:   {count:,}")
    print(f"   Range:  {date_range[0]} to {date_range[1]}")
    print(f"   Files:  {n_files}")
    print(f"   Size:   {total_size / 1e6:.1f} MB")
    print(f"   Time:   {elapsed:.1f}s")
    print(f"   Ratio:  {csv_path.stat().st_size / max(total_size, 1):.1f}x compression")

    conn.close()


if __name__ == "__main__":
    main()
