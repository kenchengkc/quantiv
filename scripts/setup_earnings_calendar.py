#!/usr/bin/env python3
"""
Setup earnings calendar for Quantiv ML pipeline.

Creates a sample earnings calendar CSV and DuckDB view for expected move modeling.
In production, this would be replaced with automated earnings data feeds.

Usage:
  python scripts/setup_earnings_calendar.py [--data-root /srv/quantiv-data]
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import csv
from datetime import datetime, timedelta
import random

def create_sample_earnings_calendar(ref_dir: Path):
    """Create a sample earnings calendar CSV with major symbols."""
    print("[earnings] Creating sample earnings calendar...")
    
    # Sample earnings dates for major tech stocks (aligned with 2022 data)
    # In production, this would come from an earnings API
    earnings_data = [
        # Q4 2022 earnings season
        ("NFLX", "2022-03-15"),
        ("MSFT", "2022-03-16"),
        ("TSLA", "2022-03-17"),
        ("IBM", "2022-03-18"),
        ("AAPL", "2022-03-19"),
        # Q1 2023 earnings season
        ("GOOGL", "2023-02-01"),
        ("AMZN", "2023-02-02"),
        ("META", "2023-02-03"),
        ("NVDA", "2023-02-15"),
        ("PYPL", "2023-02-09"),
        ("UBER", "2023-02-08"),
        ("LYFT", "2023-02-10"),
        ("SNAP", "2023-02-07"),
        ("TWTR", "2023-02-09"),
        ("SQ", "2023-02-24"),
        # Q2 2023 earnings season
        ("AAPL", "2023-04-27"),
        ("MSFT", "2023-04-26"),
        ("GOOGL", "2023-04-25"),
        ("AMZN", "2023-04-27"),
        ("TSLA", "2023-04-24"),
        ("META", "2023-04-26"),
        ("NVDA", "2023-05-24"),
        ("NFLX", "2023-04-18"),
        ("IBM", "2023-04-18"),
        ("INTC", "2023-04-27"),
        ("CRM", "2023-05-31"),
        ("ORCL", "2023-06-13"),
        ("ADBE", "2023-06-15"),
        ("PYPL", "2023-05-10"),
        ("UBER", "2023-05-09"),
        ("LYFT", "2023-05-09"),
        ("SNAP", "2023-04-21"),
        ("TWTR", "2023-04-27"),
        ("SQ", "2023-05-04"),
        # Q3 2023 earnings season
        ("AAPL", "2023-07-27"),
        ("MSFT", "2023-07-26"),
        ("GOOGL", "2023-07-25"),
        ("AMZN", "2023-07-27"),
        ("TSLA", "2023-07-24"),
        ("META", "2023-07-26"),
        ("NVDA", "2023-08-24"),
        ("NFLX", "2023-07-20"),
        ("IBM", "2023-07-19"),
        ("INTC", "2023-07-27"),
        ("CRM", "2023-08-30"),
        ("ORCL", "2023-09-12"),
        ("ADBE", "2023-09-14"),
        ("PYPL", "2023-08-02"),
        ("UBER", "2023-08-01"),
        ("LYFT", "2023-08-07"),
        ("SNAP", "2023-07-21"),
        ("TWTR", "2023-07-27"),
        ("SQ", "2023-08-02"),
        # Q4 2023 earnings season
        ("AAPL", "2023-10-26"),
        ("MSFT", "2023-10-25"),
        ("GOOGL", "2023-10-24"),
        ("AMZN", "2023-10-26"),
        ("TSLA", "2023-10-23"),
        ("META", "2023-10-25"),
        ("NVDA", "2023-11-21"),
        ("NFLX", "2023-10-19"),
        ("IBM", "2023-10-18"),
        ("INTC", "2023-10-26"),
        ("CRM", "2023-11-30"),
        ("ORCL", "2023-12-11"),
        ("ADBE", "2023-12-13"),
        ("PYPL", "2023-11-09"),
        ("UBER", "2023-11-08"),
        ("LYFT", "2023-11-07"),
        ("SNAP", "2023-10-23"),
        ("TWTR", "2023-10-26"),
        ("SQ", "2023-11-03"),
        # Q1 2024 earnings season
        ("AAPL", "2024-01-25"),
        ("MSFT", "2024-01-24"),
        ("GOOGL", "2024-01-30"),
        ("AMZN", "2024-02-01"),
        ("TSLA", "2024-01-24"),
        ("META", "2024-02-01"),
        ("NVDA", "2024-02-21"),
        ("NFLX", "2024-01-23"),
        ("IBM", "2024-01-24"),
        ("INTC", "2024-01-25"),
        ("CRM", "2024-02-29"),
        ("ORCL", "2024-03-11"),
        ("ADBE", "2024-03-14"),
        ("PYPL", "2024-01-31"),
        ("UBER", "2024-02-07"),
        ("LYFT", "2024-02-13"),
        ("SNAP", "2024-02-06"),
        ("TWTR", "2024-02-08"),
        ("SQ", "2024-02-22"),
        # Q2 2024 earnings season
        ("AAPL", "2024-04-25"),
        ("MSFT", "2024-04-24"),
        ("GOOGL", "2024-04-25"),
        ("AMZN", "2024-04-30"),
        ("TSLA", "2024-04-23"),
        ("META", "2024-04-24"),
        ("NVDA", "2024-05-22"),
        ("NFLX", "2024-04-18"),
        ("IBM", "2024-04-24"),
        ("INTC", "2024-04-25"),
        ("CRM", "2024-05-30"),
        ("ORCL", "2024-06-10"),
        ("ADBE", "2024-06-13"),
        ("PYPL", "2024-05-08"),
        ("UBER", "2024-05-08"),
        ("LYFT", "2024-05-07"),
        ("SNAP", "2024-04-25"),
        ("TWTR", "2024-04-26"),
        ("SQ", "2024-05-02"),
        # Q3 2024 earnings season
        ("AAPL", "2024-07-25"),
        ("MSFT", "2024-07-24"),
        ("GOOGL", "2024-07-23"),
        ("AMZN", "2024-07-31"),
        ("TSLA", "2024-07-23"),
        ("META", "2024-07-31"),
        ("NVDA", "2024-08-28"),
        ("NFLX", "2024-07-18"),
        ("IBM", "2024-07-24"),
        ("INTC", "2024-07-25"),
        ("CRM", "2024-08-28"),
        ("ORCL", "2024-09-09"),
        ("ADBE", "2024-09-12"),
        ("PYPL", "2024-08-07"),
        ("UBER", "2024-08-06"),
        ("LYFT", "2024-08-06"),
        ("SNAP", "2024-07-25"),
        ("TWTR", "2024-07-26"),
        ("SQ", "2024-08-01"),
        # Q4 2024 earnings season
        ("AAPL", "2024-10-24"),
        ("MSFT", "2024-10-23"),
        ("GOOGL", "2024-10-29"),
        ("AMZN", "2024-10-31"),
        ("TSLA", "2024-10-23"),
        ("META", "2024-10-30"),
        ("NVDA", "2024-11-20"),
        ("NFLX", "2024-10-17"),
        ("IBM", "2024-10-23"),
        ("INTC", "2024-10-24"),
        ("CRM", "2024-11-26"),
        ("ORCL", "2024-12-09"),
        ("ADBE", "2024-12-12"),
        ("PYPL", "2024-11-06"),
        ("UBER", "2024-11-05"),
        ("LYFT", "2024-11-05"),
        ("SNAP", "2024-10-24"),
        ("TWTR", "2024-10-25"),
        ("SQ", "2024-11-07"),
    ]
    
    # Add some 2025 dates for forward-looking predictions
    symbols_2025 = {
        'AAPL': ['2025-01-30', '2025-05-01', '2025-07-31', '2025-10-30'],
        'MSFT': ['2025-01-23', '2025-04-24', '2025-07-24', '2025-10-23'],
        'GOOGL': ['2025-01-29', '2025-04-24', '2025-07-22', '2025-10-28'],
        'AMZN': ['2025-01-31', '2025-04-29', '2025-07-31', '2025-10-30'],
        'TSLA': ['2025-01-23', '2025-04-22', '2025-07-22', '2025-10-22'],
        'META': ['2025-01-31', '2025-04-23', '2025-07-30', '2025-10-29'],
        'NVDA': ['2025-02-20', '2025-05-21', '2025-08-27', '2025-11-19'],
        'NFLX': ['2025-01-22', '2025-04-17', '2025-07-17', '2025-10-16'],
    }
    
    # Combine all earnings dates
    all_earnings = []
    
    # Add 2024 earnings
    for symbol, dates in symbols_quarters.items():
        for date_str in dates:
            all_earnings.append({'act_symbol': symbol, 'earnings_date': date_str})
    
    # Add 2025 earnings
    for symbol, dates in symbols_2025.items():
        for date_str in dates:
            all_earnings.append({'act_symbol': symbol, 'earnings_date': date_str})
    
    # Sort by date
    all_earnings.sort(key=lambda x: x['earnings_date'])
    
    # Write to CSV
    csv_path = ref_dir / "earnings_calendar.csv"
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['act_symbol', 'earnings_date'])
        writer.writeheader()
        writer.writerows(all_earnings)
    
    print(f"[earnings] Created {csv_path} with {len(all_earnings)} earnings dates")
    return csv_path

def create_earnings_view(conn, data_root: Path):
    """Create DuckDB view for earnings calendar."""
    print("[earnings] Creating DuckDB earnings view...")
    
    earnings_csv_path = data_root / "ref" / "earnings_calendar.csv"
    
    earnings_view_sql = f"""
    CREATE OR REPLACE VIEW v_earnings AS
    SELECT
      act_symbol,
      earnings_date::DATE AS earnings_date
    FROM read_csv_auto('{earnings_csv_path}', header=true)
    WHERE act_symbol IS NOT NULL 
      AND earnings_date IS NOT NULL;
    """
    
    try:
        conn.execute(earnings_view_sql)
        print("[earnings] ✓ Created v_earnings view")
        
        # Test the view
        result = conn.execute("SELECT COUNT(*) FROM v_earnings").fetchone()
        count = result[0] if result else 0
        print(f"[earnings] ✓ View contains {count} earnings dates")
        
        # Show sample data
        result = conn.execute("SELECT * FROM v_earnings ORDER BY earnings_date LIMIT 5").fetchall()
        print("[earnings] Sample earnings dates:")
        for symbol, date in result:
            print(f"  {symbol}: {date}")
            
    except Exception as e:
        print(f"[earnings] ⚠ Failed to create v_earnings view: {e}")

def create_earnings_analysis_views(conn):
    """Create additional views for earnings analysis."""
    print("[earnings] Creating earnings analysis views...")
    
    analysis_sql = """
    -- Upcoming earnings (next 30 days)
    CREATE OR REPLACE VIEW v_earnings_upcoming AS
    SELECT *
    FROM v_earnings
    WHERE earnings_date BETWEEN current_date() AND current_date() + INTERVAL '30' DAY
    ORDER BY earnings_date;
    
    -- Earnings by quarter
    CREATE OR REPLACE VIEW v_earnings_quarterly AS
    SELECT
      EXTRACT(YEAR FROM earnings_date) as year,
      EXTRACT(QUARTER FROM earnings_date) as quarter,
      COUNT(*) as earnings_count,
      COUNT(DISTINCT act_symbol) as unique_symbols
    FROM v_earnings
    GROUP BY 1, 2
    ORDER BY 1, 2;
    
    -- Earnings frequency by symbol
    CREATE OR REPLACE VIEW v_earnings_frequency AS
    SELECT
      act_symbol,
      COUNT(*) as total_earnings,
      MIN(earnings_date) as first_earnings,
      MAX(earnings_date) as last_earnings
    FROM v_earnings
    GROUP BY act_symbol
    ORDER BY total_earnings DESC;
    """
    
    try:
        conn.execute(analysis_sql)
        print("[earnings] ✓ Created earnings analysis views")
    except Exception as e:
        print(f"[earnings] ⚠ Failed to create analysis views: {e}")

def run_earnings_validation(conn):
    """Validate the earnings calendar setup."""
    print("[earnings] Running validation checks...")
    
    checks = [
        ("Total earnings dates", "SELECT COUNT(*) FROM v_earnings"),
        ("Unique symbols", "SELECT COUNT(DISTINCT act_symbol) FROM v_earnings"),
        ("Date range", "SELECT MIN(earnings_date), MAX(earnings_date) FROM v_earnings"),
        ("Upcoming earnings (30 days)", "SELECT COUNT(*) FROM v_earnings_upcoming"),
    ]
    
    for check_name, query in checks:
        try:
            result = conn.execute(query).fetchone()
            if check_name == "Date range":
                min_date, max_date = result
                print(f"  {check_name:30}: {min_date} to {max_date}")
            else:
                count = result[0] if result else 0
                print(f"  {check_name:30}: {count}")
        except Exception as e:
            print(f"  {check_name:30}: ERROR - {e}")

def main():
    parser = argparse.ArgumentParser(description="Setup earnings calendar for Quantiv")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("/srv/quantiv-data"),
        help="Root directory for data (default: /srv/quantiv-data)"
    )
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        help="Path to DuckDB file (default: {data-root}/quantiv.duckdb)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory instead of /srv/quantiv-data"
    )
    parser.add_argument(
        "--csv-only",
        action="store_true",
        help="Only create CSV file, skip DuckDB views"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_root = project_root / "data"
    else:
        data_root = args.data_root
    
    if args.duckdb_path:
        duckdb_path = args.duckdb_path
    else:
        duckdb_path = data_root / "quantiv.duckdb"
    
    print(f"[setup] Data root: {data_root}")
    print(f"[setup] DuckDB path: {duckdb_path}")
    
    # Ensure ref directory exists
    ref_dir = data_root / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Create sample earnings calendar CSV
        csv_path = create_sample_earnings_calendar(ref_dir)
        
        if not args.csv_only:
            # Connect to DuckDB and create views
            if not duckdb_path.exists():
                print(f"[warning] DuckDB file not found: {duckdb_path}")
                print("Run setup_duckdb_views.py first to create the database.")
                sys.exit(1)
            
            conn = duckdb.connect(str(duckdb_path))
            
            # Create earnings views
            create_earnings_view(conn, data_root)
            create_earnings_analysis_views(conn)
            
            # Run validation
            run_earnings_validation(conn)
            
            conn.close()
        
        print("[success] Earnings calendar setup completed successfully")
        
    except Exception as e:
        print(f"[error] Failed to setup earnings calendar: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
