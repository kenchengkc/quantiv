#!/usr/bin/env python3
"""
Direct CSV to Parquet converter for local development.

Converts CSV files directly to the expected Parquet directory structure
without going through Postgres.

Usage:
  python scripts/csv_to_parquet_direct.py --local
"""

import os
import sys
from pathlib import Path
import argparse
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime

def convert_csv_to_parquet(csv_path: Path, output_dir: Path, dataset_name: str):
    """Convert CSV to partitioned Parquet files."""
    print(f"[convert] Processing {csv_path.name} -> {dataset_name}")
    
    if not csv_path.exists():
        print(f"[convert] ⚠ CSV file not found: {csv_path}")
        return False
    
    # Read CSV in chunks to handle large files
    chunk_size = 100000
    chunk_count = 0
    
    try:
        for chunk in pd.read_csv(csv_path, chunksize=chunk_size):
            chunk_count += 1
            print(f"[convert] Processing chunk {chunk_count} ({len(chunk):,} rows)")
            
            # Convert date column to datetime
            if 'date' in chunk.columns:
                chunk['date'] = pd.to_datetime(chunk['date'], errors='coerce')
                
                # Add year/month columns for partitioning
                chunk['year'] = chunk['date'].dt.year
                chunk['month'] = chunk['date'].dt.month
                
                # Remove rows with invalid dates
                chunk = chunk.dropna(subset=['date'])
                
                if len(chunk) == 0:
                    continue
                
                # Group by year/month and save to partitioned structure
                for (year, month), group in chunk.groupby(['year', 'month']):
                    if pd.isna(year) or pd.isna(month):
                        continue
                        
                    year_int = int(year)
                    month_int = int(month)
                    
                    # Create partition directory
                    partition_dir = output_dir / dataset_name / str(year_int) / f"{month_int:02d}"
                    partition_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Remove partitioning columns from data
                    data_df = group.drop(['year', 'month'], axis=1)
                    
                    # Save to Parquet
                    filename = f"part-{chunk_count:04d}-{year_int}-{month_int:02d}.parquet"
                    parquet_path = partition_dir / filename
                    
                    data_df.to_parquet(parquet_path, index=False, compression='zstd')
                    
                    print(f"[convert] Saved {len(data_df):,} rows to {parquet_path}")
            
        print(f"[convert] ✓ Completed {csv_path.name} conversion")
        return True
        
    except Exception as e:
        print(f"[convert] ⚠ Failed to convert {csv_path.name}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Convert CSV files to Parquet")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Data directory path"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_dir = project_root / "data"
    else:
        data_dir = args.data_dir or Path("/srv/quantiv-data")
    
    print("CSV TO PARQUET CONVERTER")
    print("=" * 50)
    print(f"Data directory: {data_dir}")
    
    if not data_dir.exists():
        print(f"[error] Data directory not found: {data_dir}")
        sys.exit(1)
    
    # Output directory for Parquet files
    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(exist_ok=True)
    
    success_count = 0
    total_files = 0
    
    # Convert options chain CSV
    options_csv = data_dir / "option_chain.csv"
    if options_csv.exists():
        total_files += 1
        if convert_csv_to_parquet(options_csv, parquet_dir, "options_chain"):
            success_count += 1
    else:
        print(f"[skip] Options CSV not found: {options_csv}")
    
    # Convert volatility history CSV
    volhist_csv = data_dir / "volatility_history.csv"
    if volhist_csv.exists():
        total_files += 1
        if convert_csv_to_parquet(volhist_csv, parquet_dir, "volatility_history"):
            success_count += 1
    else:
        print(f"[skip] Volatility CSV not found: {volhist_csv}")
    
    print("\n" + "=" * 50)
    print("CONVERSION COMPLETE")
    print("=" * 50)
    print(f"Success rate: {success_count}/{total_files} files converted")
    
    if success_count == total_files and total_files > 0:
        print("✓ All CSV files converted successfully!")
        print("\nNext steps:")
        print("1. Re-run the ML pipeline setup")
        print("2. Check that DuckDB views can now access the Parquet data")
    elif total_files == 0:
        print("⚠ No CSV files found to convert")
    else:
        print(f"⚠ {total_files - success_count} conversions failed")

if __name__ == "__main__":
    main()
