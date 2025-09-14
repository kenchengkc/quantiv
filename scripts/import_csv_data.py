#!/usr/bin/env python3
"""
Import new CSV data for options_chain and volatility_history.
Handles Postgres insertion and Parquet file updates for ML pipeline.

Usage:
  python scripts/import_csv_data.py --type options_chain --csv path/to/new_data.csv
  python scripts/import_csv_data.py --type volatility_history --csv path/to/new_data.csv
  python scripts/import_csv_data.py --type both --options-csv path/to/options.csv --volatility-csv path/to/vol.csv
"""

import os
import sys
import argparse
import pandas as pd
import psycopg2
from pathlib import Path
from datetime import datetime
import json
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load environment variables
script_dir = Path(__file__).parent
project_root = script_dir.parent
env_file = project_root / "config" / ".env.local"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)

def get_postgres_connection():
    """Get PostgreSQL connection using environment variables."""
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

def validate_options_chain_csv(df: pd.DataFrame) -> bool:
    """Validate options_chain CSV structure."""
    required_columns = [
        'act_symbol', 'date', 'expiry', 'strike', 'call_put', 
        'bid', 'ask', 'last', 'vol', 'open_interest', 'delta', 
        'gamma', 'theta', 'vega', 'rho', 'iv'
    ]
    
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"[error] Missing required columns in options_chain CSV: {missing_columns}")
        return False
    
    print(f"[validation] Options chain CSV validated: {len(df)} rows, {len(df.columns)} columns")
    return True

def validate_volatility_history_csv(df: pd.DataFrame) -> bool:
    """Validate volatility_history CSV structure."""
    required_columns = [
        'act_symbol', 'date', 'iv_current', 'hv_current', 
        'iv_week_ago', 'iv_month_ago', 'iv_year_high', 'iv_year_low'
    ]
    
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"[error] Missing required columns in volatility_history CSV: {missing_columns}")
        return False
    
    print(f"[validation] Volatility history CSV validated: {len(df)} rows, {len(df.columns)} columns")
    return True

def clean_options_chain_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and prepare options_chain data for insertion."""
    print("[clean] Cleaning options chain data...")
    
    # Convert dates
    df['date'] = pd.to_datetime(df['date']).dt.date
    df['expiry'] = pd.to_datetime(df['expiry']).dt.date
    
    # Clean numeric columns
    numeric_columns = ['strike', 'bid', 'ask', 'last', 'vol', 'open_interest', 
                      'delta', 'gamma', 'theta', 'vega', 'rho', 'iv']
    
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Normalize call_put values
    df['call_put'] = df['call_put'].str.upper().map({'C': 'C', 'P': 'P', 'CALL': 'C', 'PUT': 'P'})
    
    # Remove invalid rows
    initial_count = len(df)
    df = df.dropna(subset=['act_symbol', 'date', 'expiry', 'strike', 'call_put'])
    df = df[df['call_put'].isin(['C', 'P'])]
    df = df[df['strike'] > 0]
    
    print(f"[clean] Cleaned {initial_count} -> {len(df)} rows")
    return df

def clean_volatility_history_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and prepare volatility_history data for insertion."""
    print("[clean] Cleaning volatility history data...")
    
    # Convert dates
    df['date'] = pd.to_datetime(df['date']).dt.date
    
    # Clean numeric columns
    numeric_columns = ['iv_current', 'hv_current', 'iv_week_ago', 'iv_month_ago', 
                      'iv_year_high', 'iv_year_low', 'iv_percentile', 'iv_rank']
    
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Remove invalid rows
    initial_count = len(df)
    df = df.dropna(subset=['act_symbol', 'date', 'iv_current'])
    df = df[df['iv_current'] > 0]
    
    print(f"[clean] Cleaned {initial_count} -> {len(df)} rows")
    return df

def insert_options_chain_data(df: pd.DataFrame, conn) -> int:
    """Insert options_chain data into PostgreSQL."""
    print("[postgres] Inserting options chain data...")
    
    cursor = conn.cursor()
    
    # Create temporary table for staging
    cursor.execute("""
        CREATE TEMP TABLE options_chain_temp (LIKE options_chain INCLUDING ALL)
    """)
    
    # Prepare data for insertion
    records = []
    for _, row in df.iterrows():
        record = (
            row['act_symbol'], row['date'], row['expiry'], float(row['strike']),
            row['call_put'], row.get('bid'), row.get('ask'), row.get('last'),
            row.get('vol'), row.get('open_interest'), row.get('delta'),
            row.get('gamma'), row.get('theta'), row.get('vega'), 
            row.get('rho'), row.get('iv'), datetime.now()
        )
        records.append(record)
    
    # Bulk insert into temp table
    cursor.executemany("""
        INSERT INTO options_chain_temp 
        (act_symbol, date, expiry, strike, call_put, bid, ask, last, vol, 
         open_interest, delta, gamma, theta, vega, rho, iv, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, records)
    
    # Upsert from temp table to main table (keep existing data, add new)
    cursor.execute("""
        INSERT INTO options_chain 
        SELECT * FROM options_chain_temp
        ON CONFLICT (act_symbol, date, expiry, strike, call_put) 
        DO NOTHING
    """)
    
    inserted_count = cursor.rowcount
    conn.commit()
    cursor.close()
    
    print(f"[postgres] Inserted {inserted_count} new options chain records")
    return inserted_count

def insert_volatility_history_data(df: pd.DataFrame, conn) -> int:
    """Insert volatility_history data into PostgreSQL."""
    print("[postgres] Inserting volatility history data...")
    
    cursor = conn.cursor()
    
    # Create temporary table for staging
    cursor.execute("""
        CREATE TEMP TABLE volatility_history_temp (LIKE volatility_history INCLUDING ALL)
    """)
    
    # Prepare data for insertion
    records = []
    for _, row in df.iterrows():
        record = (
            row['act_symbol'], row['date'], row.get('iv_current'),
            row.get('hv_current'), row.get('iv_week_ago'), row.get('iv_month_ago'),
            row.get('iv_year_high'), row.get('iv_year_low'), 
            row.get('iv_percentile'), row.get('iv_rank'), datetime.now()
        )
        records.append(record)
    
    # Bulk insert into temp table
    cursor.executemany("""
        INSERT INTO volatility_history_temp 
        (act_symbol, date, iv_current, hv_current, iv_week_ago, iv_month_ago,
         iv_year_high, iv_year_low, iv_percentile, iv_rank, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, records)
    
    # Upsert from temp table to main table
    cursor.execute("""
        INSERT INTO volatility_history 
        SELECT * FROM volatility_history_temp
        ON CONFLICT (act_symbol, date) 
        DO UPDATE SET
            iv_current = EXCLUDED.iv_current,
            hv_current = EXCLUDED.hv_current,
            iv_week_ago = EXCLUDED.iv_week_ago,
            iv_month_ago = EXCLUDED.iv_month_ago,
            iv_year_high = EXCLUDED.iv_year_high,
            iv_year_low = EXCLUDED.iv_year_low,
            iv_percentile = EXCLUDED.iv_percentile,
            iv_rank = EXCLUDED.iv_rank,
            created_at = EXCLUDED.created_at
    """)
    
    inserted_count = cursor.rowcount
    conn.commit()
    cursor.close()
    
    print(f"[postgres] Upserted {inserted_count} volatility history records")
    return inserted_count

def update_parquet_files(data_type: str, project_root: Path):
    """Update Parquet files from PostgreSQL data."""
    print(f"[parquet] Updating {data_type} Parquet files...")
    
    # Use the existing CSV to Parquet script
    script_path = project_root / "scripts" / "csv_to_parquet_direct.py"
    
    if not script_path.exists():
        print(f"[warning] Parquet update script not found: {script_path}")
        return False
    
    try:
        import subprocess
        result = subprocess.run([
            sys.executable, str(script_path), 
            "--local", "--update-only", f"--table={data_type}"
        ], capture_output=True, text=True, cwd=str(project_root))
        
        if result.returncode == 0:
            print(f"[parquet] ✓ Updated {data_type} Parquet files")
            return True
        else:
            print(f"[parquet] ⚠ Parquet update failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"[parquet] ⚠ Parquet update error: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Import CSV data to Postgres and update Parquet files")
    parser.add_argument("--type", choices=["options_chain", "volatility_history", "both"], 
                       required=True, help="Type of data to import")
    parser.add_argument("--csv", help="CSV file path (for single type import)")
    parser.add_argument("--options-csv", help="Options chain CSV file path")
    parser.add_argument("--volatility-csv", help="Volatility history CSV file path")
    parser.add_argument("--skip-parquet", action="store_true", 
                       help="Skip Parquet file updates")
    
    args = parser.parse_args()
    
    print("QUANTIV CSV DATA IMPORT")
    print("=" * 50)
    print(f"Import type: {args.type}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    # Validate arguments
    if args.type == "both":
        if not args.options_csv or not args.volatility_csv:
            print("[error] Both --options-csv and --volatility-csv required for 'both' type")
            sys.exit(1)
    else:
        if not args.csv:
            print("[error] --csv required for single type import")
            sys.exit(1)
    
    # Connect to PostgreSQL
    conn = get_postgres_connection()
    print("[postgres] ✓ Connected to PostgreSQL")
    
    total_inserted = 0
    
    try:
        # Import options chain data
        if args.type in ["options_chain", "both"]:
            csv_path = args.csv if args.type == "options_chain" else args.options_csv
            
            print(f"\n[import] Loading options chain CSV: {csv_path}")
            df = pd.read_csv(csv_path)
            
            if validate_options_chain_csv(df):
                df_clean = clean_options_chain_data(df)
                inserted = insert_options_chain_data(df_clean, conn)
                total_inserted += inserted
                
                if not args.skip_parquet:
                    update_parquet_files("options_chain", project_root)
        
        # Import volatility history data
        if args.type in ["volatility_history", "both"]:
            csv_path = args.csv if args.type == "volatility_history" else args.volatility_csv
            
            print(f"\n[import] Loading volatility history CSV: {csv_path}")
            df = pd.read_csv(csv_path)
            
            if validate_volatility_history_csv(df):
                df_clean = clean_volatility_history_data(df)
                inserted = insert_volatility_history_data(df_clean, conn)
                total_inserted += inserted
                
                if not args.skip_parquet:
                    update_parquet_files("volatility_history", project_root)
        
        print(f"\n✅ Import completed successfully!")
        print(f"Total records processed: {total_inserted}")
        
        if not args.skip_parquet:
            print("\nNext steps:")
            print("1. Rebuild ML features: python scripts/build_em_comprehensive.py --local")
            print("2. Retrain models: python scripts/train_baseline_models.py --local")
            print("3. Update forecasts: python scripts/populate_ml_forecasts.py --local")
        
    except Exception as e:
        print(f"\n[error] Import failed: {e}")
        conn.rollback()
        sys.exit(1)
    
    finally:
        conn.close()

if __name__ == "__main__":
    main()
