#!/usr/bin/env python3
"""
Daily builder script for Quantiv static site.
Generates weekly.json with IV expected moves for upcoming earnings.
"""

import os
import sys
import json
import csv
import duckdb
import pandas as pd
import requests
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional

def monday_of_week(d: date) -> date:
    """Get Monday of the week for given date."""
    return d - timedelta(days=d.weekday())  # Monday=0

def target_week(today: date) -> Tuple[date, date]:
    """Get target week (Mon-Fri) to display."""
    if today.weekday() >= 5:  # Sat=5, Sun=6
        base = today + timedelta(days=(7 - today.weekday()))  # next Monday
    else:
        base = monday_of_week(today)  # this Monday
    return base, base + timedelta(days=4)  # Mon..Fri

def previous_weekday(d: date) -> date:
    """Get previous trading day (approximate - excludes weekends only)."""
    if d.weekday() == 0:  # Monday
        return d - timedelta(days=3)  # Friday
    else:
        return d - timedelta(days=1)

def has_parquet_data(symbol: str, data_dir: Path) -> bool:
    """Check if ticker has options data in Parquet files."""
    parquet_pattern = str(data_dir / "parquet" / "options_chain" / "*" / "*" / "*.parquet")
    
    try:
        conn = duckdb.connect()
        result = conn.execute("""
            SELECT COUNT(*) 
            FROM read_parquet(?) 
            WHERE act_symbol = ?
            LIMIT 1
        """, [parquet_pattern, symbol]).fetchone()
        return result[0] > 0 if result else False
    except Exception:
        return False
    finally:
        conn.close()

def has_polygon_data(symbol: str, polygon_api_key: str) -> bool:
    """Check if ticker has options data available via Polygon API."""
    if not polygon_api_key:
        return False
    
    try:
        # Check if options contracts exist for this ticker
        url = f"https://api.polygon.io/v3/reference/options/contracts"
        params = {
            'underlying_ticker': symbol,
            'limit': 1,
            'apikey': polygon_api_key
        }
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return data.get('results_count', 0) > 0
        return False
    except Exception:
        return False

def validate_ticker_has_data(symbol: str, data_dir: Path, polygon_api_key: str = None) -> bool:
    """Check if ticker has options data from either Parquet files or Polygon API."""
    return has_parquet_data(symbol, data_dir) or has_polygon_data(symbol, polygon_api_key)

def load_earnings_calendar(csv_path: Path) -> List[Dict[str, Any]]:
    """Load earnings calendar from CSV."""
    earnings = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                earnings_date = datetime.strptime(row['date'], '%Y-%m-%d').date()
                earnings.append({
                    'act_symbol': row['act_symbol'],
                    'earnings_date': earnings_date,
                    'when': row.get('when', '')
                })
            except ValueError:
                continue  # Skip invalid dates
    return earnings

def get_week_earnings(earnings: List[Dict[str, Any]], week_start: date, week_end: date) -> List[Dict[str, Any]]:
    """Filter earnings for the target week."""
    week_earnings = []
    for earning in earnings:
        if week_start <= earning['earnings_date'] <= week_end:
            week_earnings.append(earning)
    return week_earnings

def compute_iv_expected_move(conn: duckdb.DuckDBPyConnection, symbol: str, earnings_date: date, t1: date, parquet_path: str) -> Optional[Dict[str, Any]]:
    """Compute IV expected move for a symbol using DuckDB."""
    
    try:
        # Query to compute IV expected move
        query = """
        WITH oc AS (
          SELECT *
          FROM read_parquet(?)
          WHERE act_symbol = ?
            AND date = ?
        ),
        expiry_choice AS (
          SELECT expiration
          FROM oc
          WHERE expiration >= ?
          ORDER BY expiration
          LIMIT 1
        ),
        candidates AS (
          SELECT o.* 
          FROM oc o
          JOIN expiry_choice e ON o.expiration = e.expiration
        ),
        spot_ref AS (
          -- Approximate spot by strike where |delta| is closest to 0.5
          SELECT strike AS spot_guess
          FROM candidates
          WHERE call_put = 'C'
          ORDER BY ABS(delta - 0.5)
          LIMIT 1
        ),
        atm_data AS (
          SELECT 
            c.strike,
            c.call_put,
            c.bid,
            c.ask,
            c.delta,
            c.expiration,
            s.spot_guess,
            (c.bid + c.ask) / 2.0 AS mid_price,
            ABS(c.strike - s.spot_guess) AS strike_distance
          FROM candidates c, spot_ref s
          WHERE c.call_put IN ('C', 'P')
        ),
        atm_strike AS (
          SELECT strike
          FROM atm_data
          ORDER BY strike_distance
          LIMIT 1
        ),
        final_data AS (
          SELECT 
            a.spot_guess,
            a.expiration AS chosen_expiry,
            atm.strike AS atm_strike,
            SUM(CASE WHEN a.call_put = 'C' THEN a.mid_price ELSE 0 END) AS mid_call,
            SUM(CASE WHEN a.call_put = 'P' THEN a.mid_price ELSE 0 END) AS mid_put,
            AVG(CASE WHEN a.call_put = 'C' THEN a.bid ELSE NULL END) AS call_bid,
            AVG(CASE WHEN a.call_put = 'C' THEN a.ask ELSE NULL END) AS call_ask,
            AVG(CASE WHEN a.call_put = 'P' THEN a.bid ELSE NULL END) AS put_bid,
            AVG(CASE WHEN a.call_put = 'P' THEN a.ask ELSE NULL END) AS put_ask
          FROM atm_data a
          JOIN atm_strike atm ON a.strike = atm.strike
          GROUP BY a.spot_guess, a.expiration, atm.strike
        )
        SELECT 
          spot_guess,
          chosen_expiry,
          atm_strike,
          mid_call,
          mid_put,
          (mid_call + mid_put) AS em_abs,
          CASE 
            WHEN spot_guess > 0 THEN (mid_call + mid_put) / spot_guess 
            ELSE NULL 
          END AS em_pct,
          call_bid,
          call_ask,
          put_bid,
          put_ask
        FROM final_data
        """
        
        result = conn.execute(query, [
            parquet_path, symbol, t1.strftime('%Y-%m-%d'), earnings_date.strftime('%Y-%m-%d')
        ]).fetchone()
        
        if not result:
            return None
            
        (spot_guess, chosen_expiry, atm_strike, mid_call, mid_put, em_abs, em_pct,
         call_bid, call_ask, put_bid, put_ask) = result
        
        return {
            'act_symbol': symbol,
            'earnings_date': earnings_date.isoformat(),
            't1': t1.isoformat(),
            'expiry': chosen_expiry.isoformat() if chosen_expiry else None,
            'spot_ref': float(spot_guess) if spot_guess else None,
            'atm_strike': float(atm_strike) if atm_strike else None,
            'mid_call': float(mid_call) if mid_call else None,
            'mid_put': float(mid_put) if mid_put else None,
            'em_abs': float(em_abs) if em_abs else None,
            'em_pct': float(em_pct) if em_pct else None,
            'call_bid': float(call_bid) if call_bid else None,
            'call_ask': float(call_ask) if call_ask else None,
            'put_bid': float(put_bid) if put_bid else None,
            'put_ask': float(put_ask) if put_ask else None
        }
        
    except Exception as e:
        print(f"Error computing EM for {symbol}: {e}")
        return None

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

def main():
    # Setup paths
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / "data"
    earnings_csv = data_dir / "earnings_calendar.csv"
    parquet_pattern = str(data_dir / "parquet" / "options_chain" / "*" / "*" / "*.parquet")
    output_path = repo_root / "public" / "weekly.json"
    env_path = repo_root / "config" / ".env.local"
    
    # Load environment variables from config/.env.local
    env_vars = load_env_file(env_path)
    
    # Ensure output directory exists
    output_path.parent.mkdir(exist_ok=True)
    
    print("🚀 Building weekly earnings expected moves...")
    
    # Determine target week
    today = date.today()
    week_start, week_end = target_week(today)
    print(f"Target week: {week_start} to {week_end}")
    
    # Load earnings calendar
    if not earnings_csv.exists():
        print(f"❌ Earnings calendar not found: {earnings_csv}")
        sys.exit(1)
    
    print("📅 Loading earnings calendar...")
    all_earnings = load_earnings_calendar(earnings_csv)
    week_earnings = get_week_earnings(all_earnings, week_start, week_end)
    
    print(f"Found {len(week_earnings)} earnings this week")
    
    if not week_earnings:
        print("No earnings found for target week")
        # Still generate empty JSON
        output_data = {
            "window": {
                "start": week_start.isoformat(),
                "end": week_end.isoformat(),
                "generated_at": datetime.now().isoformat()
            },
            "events": []
        }
        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)
        return
    
    # Connect to DuckDB
    print("🦆 Connecting to DuckDB...")
    conn = duckdb.connect()
    
    # Get Polygon API key from config file
    polygon_api_key = env_vars.get('POLYGON_API_KEY')
    
    # Process each earnings event
    events = []
    for earning in week_earnings:
        symbol = earning['act_symbol']
        earnings_date = earning['earnings_date']
        t1 = previous_weekday(earnings_date)
        
        print(f"Processing {symbol} (earnings: {earnings_date}, T-1: {t1})")
        
        # First validate that ticker has options data available
        if not validate_ticker_has_data(symbol, data_dir, polygon_api_key):
            print(f"  ❌ No options data available for {symbol} - skipping")
            continue
        
        # Compute IV expected move
        em_data = compute_iv_expected_move(conn, symbol, earnings_date, t1, parquet_pattern)
        
        if em_data:
            # Add timing info
            em_data['when'] = earning['when']
            events.append(em_data)
            print(f"  ✓ EM: {em_data['em_pct']:.1%} (${em_data['em_abs']:.2f})")
        else:
            print(f"  ❌ No options data found")
    
    # Sort events by earnings date
    events.sort(key=lambda x: x['earnings_date'])
    
    # Generate output JSON
    output_data = {
        "window": {
            "start": week_start.isoformat(),
            "end": week_end.isoformat(),
            "generated_at": datetime.now().isoformat()
        },
        "events": events,
        "summary": {
            "total_events": len(events),
            "avg_em_pct": sum(e['em_pct'] for e in events if e['em_pct']) / len([e for e in events if e['em_pct']]) if events else 0
        }
    }
    
    # Write output
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    print(f"✅ Generated {output_path} with {len(events)} events")
    print(f"Average expected move: {output_data['summary']['avg_em_pct']:.1%}")

if __name__ == "__main__":
    main()
