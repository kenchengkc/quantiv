#!/usr/bin/env python3
"""
Sync PostgreSQL data to Parquet files for ML pipeline.
Handles incremental updates and maintains partitioned structure.

Usage:
  python scripts/sync_postgres_to_parquet.py --local
  python scripts/sync_postgres_to_parquet.py --local --table options_chain
  python scripts/sync_postgres_to_parquet.py --local --incremental --days 7
"""

import os
import sys
import argparse
import pandas as pd
import psycopg2
from pathlib import Path
from datetime import datetime, timedelta
import pyarrow as pa
import pyarrow.parquet as pq
from typing import Optional, List
from dotenv import load_dotenv

# Load environment variables
script_dir = Path(__file__).parent
project_root = script_dir.parent
env_file = project_root / "config" / ".env.local"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)

def get_postgres_connection():
    """Get PostgreSQL connection."""
    try:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "quantiv_user"),
            password=os.getenv("POSTGRES_PASSWORD", "quantiv_secure_2024"),
            database=os.getenv("POSTGRES_DB", "quantiv_options")
        )
        return conn
    except Exception as e:
        print(f"[error] Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

def get_table_data(conn, table_name: str, incremental: bool = False, days: int = 7) -> pd.DataFrame:
    """Extract data from PostgreSQL table."""
    print(f"[extract] Loading {table_name} data from PostgreSQL...")
    
    if incremental:
        date_filter = f"WHERE date >= CURRENT_DATE - INTERVAL '{days} days'"
        print(f"[extract] Incremental mode: last {days} days")
    else:
        date_filter = ""
        print("[extract] Full sync mode")
    
    if table_name == "options_chain":
        query = f"""
            SELECT 
                act_symbol, date, expiry, strike, call_put,
                bid, ask, last, vol, open_interest,
                delta, gamma, theta, vega, rho, iv,
                created_at
            FROM options_chain 
            {date_filter}
            ORDER BY act_symbol, date, expiry, strike, call_put
        """
    elif table_name == "volatility_history":
        query = f"""
            SELECT 
                act_symbol, date, iv_current, hv_current,
                iv_week_ago, iv_month_ago, iv_year_high, iv_year_low,
                iv_percentile, iv_rank, created_at
            FROM volatility_history 
            {date_filter}
            ORDER BY act_symbol, date
        """
    else:
        raise ValueError(f"Unsupported table: {table_name}")
    
    df = pd.read_sql_query(query, conn)
    print(f"[extract] Loaded {len(df):,} records from {table_name}")
    
    return df

def create_partitioned_parquet(df: pd.DataFrame, table_name: str, data_dir: Path, incremental: bool = False):
    """Create or update partitioned Parquet files."""
    print(f"[parquet] Creating partitioned Parquet files for {table_name}...")
    
    if df.empty:
        print("[parquet] No data to write")
        return
    
    # Prepare partitioning columns
    df['date'] = pd.to_datetime(df['date'])
    df['year'] = df['date'].dt.year
    df['month'] = df['date'].dt.month
    
    # Set up output directory
    output_dir = data_dir / "parquet" / table_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Group by partitions and write files
    partitions_written = 0
    
    for (symbol, year, month), group_df in df.groupby(['act_symbol', 'year', 'month']):
        # Create partition directory structure
        partition_dir = output_dir / f"underlying={symbol}" / f"quote_year={year}" / f"quote_month={month:02d}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        # Prepare data for writing
        write_df = group_df.drop(['year', 'month'], axis=1)
        
        # File path
        file_path = partition_dir / f"{table_name}_{symbol}_{year}_{month:02d}.parquet"
        
        if incremental and file_path.exists():
            # For incremental updates, merge with existing data
            try:
                existing_df = pd.read_parquet(file_path)
                
                # Combine and deduplicate
                if table_name == "options_chain":
                    # Deduplicate on primary key
                    combined_df = pd.concat([existing_df, write_df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(
                        subset=['act_symbol', 'date', 'expiry', 'strike', 'call_put'],
                        keep='last'
                    )
                else:  # volatility_history
                    combined_df = pd.concat([existing_df, write_df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(
                        subset=['act_symbol', 'date'],
                        keep='last'
                    )
                
                write_df = combined_df.sort_values(['act_symbol', 'date'])
                
            except Exception as e:
                print(f"[warning] Failed to merge with existing file {file_path}: {e}")
        
        # Write Parquet file
        try:
            write_df.to_parquet(
                file_path,
                engine='pyarrow',
                compression='snappy',
                index=False
            )
            partitions_written += 1
            
        except Exception as e:
            print(f"[error] Failed to write {file_path}: {e}")
    
    print(f"[parquet] ✓ Written {partitions_written} partition files for {table_name}")

def update_metadata(table_name: str, data_dir: Path, record_count: int):
    """Update metadata file with sync information."""
    metadata_file = data_dir / "parquet" / f"{table_name}_metadata.json"
    
    metadata = {
        "table_name": table_name,
        "last_sync": datetime.now().isoformat(),
        "record_count": record_count,
        "compression": "snappy",
        "partitioning": "underlying/quote_year/quote_month",
        "format_version": "2.0"
    }
    
    with open(metadata_file, 'w') as f:
        import json
        json.dump(metadata, f, indent=2)
    
    print(f"[metadata] Updated {metadata_file}")

def validate_parquet_files(table_name: str, data_dir: Path) -> bool:
    """Validate created Parquet files."""
    print(f"[validate] Validating {table_name} Parquet files...")
    
    parquet_dir = data_dir / "parquet" / table_name
    if not parquet_dir.exists():
        print(f"[validate] ⚠ Parquet directory not found: {parquet_dir}")
        return False
    
    # Count files and estimate records
    parquet_files = list(parquet_dir.rglob("*.parquet"))
    total_records = 0
    
    for file_path in parquet_files[:5]:  # Sample first 5 files
        try:
            df = pd.read_parquet(file_path)
            total_records += len(df)
        except Exception as e:
            print(f"[validate] ⚠ Failed to read {file_path}: {e}")
            return False
    
    print(f"[validate] ✓ Found {len(parquet_files)} Parquet files")
    print(f"[validate] ✓ Sample validation: {total_records} records in first 5 files")
    
    return True

def main():
    parser = argparse.ArgumentParser(description="Sync PostgreSQL to Parquet files")
    parser.add_argument("--local", action="store_true", help="Use local data directory")
    parser.add_argument("--table", choices=["options_chain", "volatility_history", "both"], 
                       default="both", help="Table to sync")
    parser.add_argument("--incremental", action="store_true", 
                       help="Incremental sync (only recent data)")
    parser.add_argument("--days", type=int, default=7, 
                       help="Days to sync for incremental mode")
    
    args = parser.parse_args()
    
    if args.local:
        data_dir = project_root / "data"
    else:
        data_dir = Path("/srv/quantiv-data")
    
    print("POSTGRES TO PARQUET SYNC")
    print("=" * 50)
    print(f"Data dir: {data_dir}")
    print(f"Table: {args.table}")
    print(f"Mode: {'Incremental' if args.incremental else 'Full'}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    # Connect to PostgreSQL
    conn = get_postgres_connection()
    print("[postgres] ✓ Connected to PostgreSQL")
    
    try:
        tables_to_sync = []
        if args.table == "both":
            tables_to_sync = ["options_chain", "volatility_history"]
        else:
            tables_to_sync = [args.table]
        
        for table_name in tables_to_sync:
            print(f"\n{'='*30} {table_name.upper()} {'='*30}")
            
            # Extract data
            df = get_table_data(conn, table_name, args.incremental, args.days)
            
            if df.empty:
                print(f"[skip] No data found for {table_name}")
                continue
            
            # Create Parquet files
            create_partitioned_parquet(df, table_name, data_dir, args.incremental)
            
            # Update metadata
            update_metadata(table_name, data_dir, len(df))
            
            # Validate
            validate_parquet_files(table_name, data_dir)
        
        print(f"\n✅ Sync completed successfully!")
        print("\nNext steps:")
        print("1. Rebuild ML features: python scripts/build_em_comprehensive.py --local")
        print("2. Update forecasts: python scripts/populate_ml_forecasts.py --local")
        
    except Exception as e:
        print(f"\n[error] Sync failed: {e}")
        sys.exit(1)
    
    finally:
        conn.close()

if __name__ == "__main__":
    main()
