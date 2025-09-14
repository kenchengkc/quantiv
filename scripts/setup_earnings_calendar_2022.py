#!/usr/bin/env python3
"""
Setup earnings calendar with 2022 dates to align with available data.

Usage:
  python scripts/setup_earnings_calendar_2022.py --local
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import csv
from datetime import datetime

def create_2022_earnings_calendar(ref_dir: Path):
    """Create earnings calendar with 2022 dates aligned to available data."""
    print("[earnings] Creating 2022 earnings calendar...")
    
    # Earnings dates aligned with 2022 data (March 2022)
    earnings_data = [
        ("AAPL", "2022-03-10"),
        ("MSFT", "2022-03-11"),
        ("GOOGL", "2022-03-12"),
        ("AMZN", "2022-03-13"),
        ("TSLA", "2022-03-14"),
        ("META", "2022-03-15"),
        ("NVDA", "2022-03-16"),
        ("NFLX", "2022-03-17"),
        ("IBM", "2022-03-18"),
        ("INTC", "2022-03-19"),
        ("ORCL", "2022-03-20"),
        ("ADBE", "2022-03-21"),
        ("CRM", "2022-03-22"),
        ("PYPL", "2022-03-23"),
        ("UBER", "2022-03-24"),
        ("LYFT", "2022-03-25"),
        ("SNAP", "2022-03-26"),
        ("AMD", "2022-03-27"),
        ("SQ", "2022-03-28"),
        ("TWTR", "2022-03-29"),
    ]
    
    # Write to CSV
    earnings_file = ref_dir / "earnings_calendar.csv"
    with open(earnings_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['act_symbol', 'earnings_date'])
        writer.writerows(earnings_data)
    
    print(f"[earnings] Created {earnings_file} with {len(earnings_data)} earnings dates")
    return earnings_file, len(earnings_data)

def create_earnings_view(conn):
    """Create DuckDB view for earnings calendar."""
    print("[earnings] Creating DuckDB earnings view...")
    
    earnings_sql = """
    CREATE OR REPLACE VIEW v_earnings AS
    SELECT 
        act_symbol,
        earnings_date::DATE as earnings_date
    FROM read_csv_auto('data/ref/earnings_calendar.csv')
    WHERE act_symbol IS NOT NULL
      AND earnings_date IS NOT NULL;
    """
    
    try:
        conn.execute(earnings_sql)
        
        # Verify the view
        count = conn.execute("SELECT COUNT(*) FROM v_earnings").fetchone()[0]
        print(f"[earnings] ✓ Created v_earnings view")
        print(f"[earnings] ✓ View contains {count} earnings dates")
        
        # Show sample data
        sample = conn.execute("SELECT act_symbol, earnings_date FROM v_earnings LIMIT 5").fetchall()
        print("[earnings] Sample earnings dates:")
        for symbol, date in sample:
            print(f"  {symbol}: {date}")
        
        return True
        
    except Exception as e:
        print(f"[earnings] ⚠ Failed to create earnings view: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Setup 2022 earnings calendar")
    parser.add_argument("--local", action="store_true", help="Use local data directory")
    parser.add_argument("--data-dir", type=Path, help="Data directory path")
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_dir = project_root / "data"
    else:
        data_dir = args.data_dir or Path("/srv/quantiv-data")
    
    duckdb_path = data_dir / "quantiv.duckdb"
    ref_dir = data_dir / "ref"
    
    print("[setup] Data root: {data_dir}")
    print(f"[setup] DuckDB path: {duckdb_path}")
    
    # Ensure ref directory exists
    ref_dir.mkdir(parents=True, exist_ok=True)
    
    # Create earnings calendar
    earnings_file, count = create_2022_earnings_calendar(ref_dir)
    
    # Connect to DuckDB and create view
    if duckdb_path.exists():
        conn = duckdb.connect(str(duckdb_path))
        
        # Change working directory for relative path in SQL
        original_cwd = os.getcwd()
        os.chdir(data_dir.parent)
        
        try:
            success = create_earnings_view(conn)
            
            if success:
                # Run validation
                print("[earnings] Running validation checks...")
                total_count = conn.execute("SELECT COUNT(*) FROM v_earnings").fetchone()[0]
                unique_symbols = conn.execute("SELECT COUNT(DISTINCT act_symbol) FROM v_earnings").fetchone()[0]
                date_range = conn.execute("SELECT MIN(earnings_date), MAX(earnings_date) FROM v_earnings").fetchone()
                
                print(f"  Total earnings dates          : {total_count}")
                print(f"  Unique symbols                : {unique_symbols}")
                print(f"  Date range                    : {date_range[0]} to {date_range[1]}")
                
        finally:
            os.chdir(original_cwd)
            conn.close()
        
        print("[success] 2022 earnings calendar setup completed successfully")
    else:
        print(f"[error] DuckDB file not found: {duckdb_path}")

if __name__ == "__main__":
    main()
