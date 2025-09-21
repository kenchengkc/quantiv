#!/usr/bin/env python3
"""
GitHub Actions compatible weekly builder.
Generates weekly.json using only earnings_calendar.csv and Polygon API.
No Parquet files required - suitable for CI/CD environments.
"""

import os
import sys
import json
import duckdb
import pandas as pd
import requests
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional

def load_env_file(env_path: Path) -> Dict[str, str]:
    """Load environment variables from .env file."""
    env_vars = {}
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars

def monday_of_week(d: date) -> date:
    """Get Monday of the week for given date."""
    return d - timedelta(days=d.weekday())

def get_current_week() -> tuple[date, date]:
    """Get current week Monday-Friday."""
    today = date.today()
    monday = monday_of_week(today)
    friday = monday + timedelta(days=4)
    return monday, friday

def build_earnings_events_table(conn: duckdb.DuckDBPyConnection, earnings_csv: Path):
    """Build canonical earnings_events table from CSV."""
    
    print("📅 Building earnings_events table...")
    
    conn.execute("""
        CREATE OR REPLACE TABLE earnings_events AS
        SELECT 
            act_symbol as ticker,
            CAST(date AS DATE) as earnings_dt,
            CASE 
                WHEN "when" = 'BMO' THEN 'before_market_open'
                WHEN "when" = 'AMC' THEN 'after_market_close'
                ELSE 'unknown'
            END as timing,
            'earnings_calendar_csv' as source,
            CASE 
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (1,2,3) THEN 'Q1'
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (4,5,6) THEN 'Q2'
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (7,8,9) THEN 'Q3'
                ELSE 'Q4'
            END as fiscal_q
        FROM read_csv(?, header=true, columns={'act_symbol': 'VARCHAR', 'date': 'VARCHAR', 'when': 'VARCHAR'})
        WHERE date IS NOT NULL 
        AND act_symbol IS NOT NULL
    """, [str(earnings_csv)])
    
    count = conn.execute("SELECT COUNT(*) FROM earnings_events").fetchone()[0]
    print(f"✅ Loaded {count:,} earnings events")

def get_week_earnings(conn: duckdb.DuckDBPyConnection, week_start: date, week_end: date) -> List[Dict[str, Any]]:
    """Get earnings events for the target week."""
    
    result = conn.execute("""
        SELECT ticker, earnings_dt, timing, fiscal_q
        FROM earnings_events 
        WHERE earnings_dt BETWEEN ? AND ?
        ORDER BY earnings_dt, ticker
    """, [week_start, week_end]).fetchall()
    
    earnings = []
    for row in result:
        ticker, earnings_dt, timing, fiscal_q = row
        earnings.append({
            'ticker': ticker,
            'earnings_dt': earnings_dt,
            'timing': timing,
            'fiscal_q': fiscal_q
        })
    
    return earnings

def validate_ticker_with_polygon(ticker: str, api_key: str) -> bool:
    """Validate ticker exists and has options using Polygon API."""
    
    try:
        # Check if ticker has options contracts
        url = f"https://api.polygon.io/v3/reference/options/contracts"
        params = {
            'underlying_ticker': ticker,
            'limit': 1,
            'apikey': api_key
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return len(data.get('results', [])) > 0
        else:
            print(f"  ⚠️  Polygon API error for {ticker}: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ⚠️  Error validating {ticker}: {e}")
        return False

def create_mock_expected_move(ticker: str, earnings_dt: date, timing: str, fiscal_q: str, 
                            as_of_date: date) -> Dict[str, Any]:
    """Create a placeholder expected move entry for tickers without options data."""
    
    lead_time_days = (earnings_dt - as_of_date).days
    
    return {
        'ticker': ticker,
        'earnings_date': earnings_dt.isoformat(),
        'timing': timing,
        'fiscal_q': fiscal_q,
        'as_of_date': as_of_date.isoformat(),
        'lead_time_days': lead_time_days,
        'data_source': 'placeholder',
        'note': 'Options data not available - placeholder entry'
    }

def compute_weekly_events(week_earnings: List[Dict[str, Any]], as_of_date: date, 
                         api_key: Optional[str]) -> List[Dict[str, Any]]:
    """Compute expected moves for weekly earnings events."""
    
    events = []
    
    for earning in week_earnings:
        ticker = earning['ticker']
        earnings_dt = earning['earnings_dt']
        timing = earning['timing']
        fiscal_q = earning['fiscal_q']
        
        print(f"Processing {ticker} (earnings: {earnings_dt})")
        
        # Validate ticker has options (if API key available)
        has_options = True
        if api_key:
            has_options = validate_ticker_with_polygon(ticker, api_key)
            if not has_options:
                print(f"  ❌ No options data for {ticker}")
        
        # Create placeholder entry
        event = create_mock_expected_move(ticker, earnings_dt, timing, fiscal_q, as_of_date)
        events.append(event)
        
        if has_options:
            print(f"  ✅ Added placeholder for {ticker}")
        else:
            print(f"  ⚠️  Added placeholder for {ticker} (no options)")
    
    return events

def main():
    # Setup paths
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / "data"
    earnings_csv = data_dir / "earnings_calendar.csv"
    output_path = repo_root / "public" / "weekly.json"
    env_path = repo_root / "config" / ".env.local"
    
    # Load environment variables
    env_vars = load_env_file(env_path)
    api_key = env_vars.get('POLYGON_API_KEY') or os.getenv('POLYGON_API_KEY')
    
    # Ensure output directory exists
    output_path.parent.mkdir(exist_ok=True)
    
    print("🚀 Building weekly earnings expected moves (GitHub Actions)...")
    
    # Get current week
    week_start, week_end = get_current_week()
    as_of_date = date.today()
    
    print(f"Target week: {week_start} to {week_end}")
    print(f"As of date: {as_of_date}")
    
    # Check required files
    if not earnings_csv.exists():
        print(f"❌ Earnings calendar not found: {earnings_csv}")
        sys.exit(1)
    
    # Connect to DuckDB (in-memory)
    conn = duckdb.connect()
    
    try:
        # Build earnings events table
        build_earnings_events_table(conn, earnings_csv)
        
        # Get earnings for target week
        week_earnings = get_week_earnings(conn, week_start, week_end)
        print(f"Found {len(week_earnings)} earnings events this week")
        
        if not week_earnings:
            print("No earnings found for target week")
            # Generate empty JSON
            output_data = {
                "metadata": {
                    "version": "github_actions_v1",
                    "generated_at": datetime.now().isoformat(),
                    "as_of_date": as_of_date.isoformat(),
                    "method": "placeholder_github"
                },
                "window": {
                    "start": week_start.isoformat(),
                    "end": week_end.isoformat()
                },
                "events": []
            }
        else:
            # Compute expected moves
            events = compute_weekly_events(week_earnings, as_of_date, api_key)
            
            # Build output JSON
            output_data = {
                "metadata": {
                    "version": "github_actions_v1",
                    "generated_at": datetime.now().isoformat(),
                    "as_of_date": as_of_date.isoformat(),
                    "method": "placeholder_github",
                    "note": "Placeholder data - full calculations require Parquet files"
                },
                "window": {
                    "start": week_start.isoformat(),
                    "end": week_end.isoformat()
                },
                "events": events
            }
        
        # Write output
        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"✅ Generated weekly.json with {len(output_data['events'])} events")
        print(f"📄 Output: {output_path}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
