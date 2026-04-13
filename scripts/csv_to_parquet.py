#!/usr/bin/env python3
"""
Convert the local option_chain.csv to date-partitioned Parquet files.

Uses DuckDB's native Hive-partitioned COPY for maximum speed — processes
the 8GB CSV in a single pass, writing partitioned Parquet in minutes.

Output: data/parquet/options_chain/year=YYYY/month=MM/*.parquet

Usage:
  python scripts/csv_to_parquet.py
  python scripts/csv_to_parquet.py --start-date 2023-04-01
"""

import argparse
import os
import time
from pathlib import Path

import duckdb


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def main():
    parser = argparse.ArgumentParser(description="Convert option_chain CSV to partitioned Parquet")
    parser.add_argument("--start-date", type=str, default=None, help="Only convert from this date onward")
    parser.add_argument("--csv", type=str, default=None, help="Path to CSV file")
    args = parser.parse_args()

    data_dir = get_data_dir()
    csv_path = Path(args.csv) if args.csv else data_dir / "option_chain.csv"
    parquet_root = data_dir / "parquet" / "options_chain"

    if not csv_path.exists():
        print(f"❌ CSV not found: {csv_path}")
        return

    print(f"CSV:          {csv_path} ({csv_path.stat().st_size / 1e9:.1f} GB)")
    print(f"Parquet root: {parquet_root}")
    parquet_root.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(":memory:")
    conn.execute("SET threads = 4")
    conn.execute("SET memory_limit = '6GB'")

    where = f"WHERE date >= '{args.start_date}'" if args.start_date else ""
    if args.start_date:
        print(f"Filtering:    date >= {args.start_date}")

    # Single-pass: read CSV, add partition columns, write Hive-partitioned Parquet
    print("\nConverting (single-pass bulk write)...")
    t0 = time.time()

    conn.execute(f"""
        COPY (
            SELECT
                date,
                act_symbol,
                expiration,
                CAST(strike AS DOUBLE) as strike,
                call_put,
                CAST(bid AS DOUBLE) as bid,
                CAST(ask AS DOUBLE) as ask,
                CAST(vol AS DOUBLE) as vol,
                CAST(delta AS DOUBLE) as delta,
                CAST(gamma AS DOUBLE) as gamma,
                CAST(theta AS DOUBLE) as theta,
                CAST(vega AS DOUBLE) as vega,
                CAST(rho AS DOUBLE) as rho,
                YEAR(date) AS year,
                LPAD(CAST(MONTH(date) AS VARCHAR), 2, '0') AS month
            FROM read_csv_auto('{csv_path}')
            {where}
        ) TO '{parquet_root}'
        (FORMAT PARQUET, COMPRESSION SNAPPY, PARTITION_BY (year, month), OVERWRITE_OR_IGNORE)
    """)

    elapsed = time.time() - t0
    print(f"Bulk write complete ({elapsed:.1f}s)")

    # Count what was written
    count = conn.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{parquet_root}/**/*.parquet', hive_partitioning=true)
    """).fetchone()[0]

    n_files = sum(1 for _ in parquet_root.rglob("*.parquet"))
    total_size = sum(f.stat().st_size for f in parquet_root.rglob("*.parquet"))

    print(f"\n✅ Conversion complete")
    print(f"   Rows:   {count:,}")
    print(f"   Files:  {n_files}")
    print(f"   Size:   {total_size / 1e9:.2f} GB")
    print(f"   Time:   {elapsed / 60:.1f} minutes")
    print(f"   Ratio:  {csv_path.stat().st_size / max(total_size, 1):.1f}x compression")

    conn.close()


if __name__ == "__main__":
    main()
