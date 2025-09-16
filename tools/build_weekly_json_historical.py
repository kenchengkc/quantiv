#!/usr/bin/env python3
"""
Historical weekly builder for testing frontend.
Uses historical earnings dates that align with our Parquet data (through Aug 13, 2025).
"""

import os
import sys
import json
import duckdb
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional

# Import our math baseline functions
sys.path.append(str(Path(__file__).parent))
from math_baseline import compute_em_math, load_env_file
from build_earnings_events import build_earnings_events_table, create_duckdb_views

def get_historical_week_with_data() -> Tuple[date, date]:
    """Get a historical week that has both earnings and options data."""
    # Use week of Aug 11-15, 2025 (our data goes through Aug 13)
    week_start = date(2025, 8, 11)  # Monday
    week_end = date(2025, 8, 15)    # Friday
    return week_start, week_end

def get_week_earnings(conn: duckdb.DuckDBPyConnection, week_start: date, week_end: date) -> List[Dict[str, Any]]:
    """Get earnings events for the target week."""
    
    result = conn.execute("""
        SELECT 
            ticker,
            earnings_dt,
            timing,
            fiscal_q
        FROM earnings_events
        WHERE earnings_dt >= ?
        AND earnings_dt <= ?
        ORDER BY earnings_dt, ticker
    """, [week_start, week_end]).fetchall()
    
    earnings = []
    for ticker, earnings_dt, timing, fiscal_q in result:
        earnings.append({
            'ticker': ticker,
            'earnings_dt': earnings_dt,
            'timing': timing,
            'fiscal_q': fiscal_q
        })
    
    return earnings

def has_options_data(conn: duckdb.DuckDBPyConnection, ticker: str, as_of_date: date) -> bool:
    """Check if ticker has options data available on as_of_date."""
    
    result = conn.execute("""
        SELECT COUNT(*) 
        FROM v_options_chain
        WHERE ticker = ?
        AND as_of_date = ?
        LIMIT 1
    """, [ticker, as_of_date]).fetchone()
    
    return result[0] > 0 if result else False

def compute_weekly_events(conn: duckdb.DuckDBPyConnection, week_earnings: List[Dict[str, Any]], 
                         as_of_date: date) -> List[Dict[str, Any]]:
    """Compute expected moves for weekly earnings events."""
    
    events = []
    
    for earning in week_earnings:
        ticker = earning['ticker']
        earnings_dt = earning['earnings_dt']
        
        print(f"Processing {ticker} (earnings: {earnings_dt})")
        
        # Check if we have options data
        if not has_options_data(conn, ticker, as_of_date):
            print(f"  ❌ No options data available for {ticker} on {as_of_date}")
            continue
        
        # Compute math baseline EM
        em_data = compute_em_math(conn, ticker, as_of_date, earnings_dt)
        
        if em_data:
            # Add additional metadata
            event = {
                'ticker': ticker,
                'earnings_date': earnings_dt.isoformat(),
                'timing': earning['timing'],
                'fiscal_q': earning['fiscal_q'],
                'as_of_date': as_of_date.isoformat(),
                'lead_time_days': em_data['lead_time_days'],
                'expiry_date': em_data['expiry_date'],
                'days_to_expiry': em_data['days_to_expiry'],
                
                # Core EM metrics
                'em_straddle_pct': em_data['em_baseline_straddle'],
                'em_iv_pct': em_data['em_baseline_iv'],
                'em_straddle_abs': em_data['straddle_price'],
                
                # Options data
                'spot_price': em_data['estimated_spot'],
                'atm_strike': em_data['atm_strike'],
                'atm_iv': em_data['avg_iv'],
                'call_iv': em_data['call_iv'],
                'put_iv': em_data['put_iv'],
                
                # Market microstructure
                'skew_atm': em_data['skew_atm'],
                'term_slope': em_data['term_slope'],
                'total_vega': em_data['total_vega'],
                'avg_bid_ask_spread': em_data['avg_bid_ask_spread'],
                
                # Metadata
                'method': 'math_baseline',
                'confidence': 'medium'
            }
            
            events.append(event)
            
            # Display results
            straddle_pct = em_data['em_baseline_straddle'] or 0
            iv_pct = em_data['em_baseline_iv'] or 0
            print(f"  ✅ T-{em_data['lead_time_days']} | Straddle: {straddle_pct:.1%} | IV: {iv_pct:.1%}")
            
        else:
            print(f"  ❌ Could not compute EM for {ticker}")
    
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
    
    # Ensure output directory exists
    output_path.parent.mkdir(exist_ok=True)
    
    print("🚀 Building historical weekly earnings expected moves...")
    
    # Use historical week with data
    week_start, week_end = get_historical_week_with_data()
    as_of_date = date(2025, 8, 13)  # Last day we have options data
    
    print(f"Historical week: {week_start} to {week_end}")
    print(f"As of date: {as_of_date}")
    
    # Check required files
    if not earnings_csv.exists():
        print(f"❌ Earnings calendar not found: {earnings_csv}")
        sys.exit(1)
    
    # Connect to DuckDB (in-memory)
    conn = duckdb.connect()
    
    try:
        # Build earnings events table and views
        print("📊 Setting up data structures...")
        build_earnings_events_table(conn, earnings_csv)
        create_duckdb_views(conn, data_dir)
        
        # Get earnings for target week
        week_earnings = get_week_earnings(conn, week_start, week_end)
        print(f"Found {len(week_earnings)} earnings events this week")
        
        if not week_earnings:
            print("No earnings found for target week")
            # Generate empty JSON
            output_data = {
                "metadata": {
                    "version": "v2_math_baseline_historical",
                    "generated_at": datetime.now().isoformat(),
                    "as_of_date": as_of_date.isoformat(),
                    "method": "math_baseline"
                },
                "window": {
                    "start": week_start.isoformat(),
                    "end": week_end.isoformat()
                },
                "events": [],
                "summary": {
                    "total_events": 0,
                    "avg_em_straddle_pct": 0,
                    "avg_em_iv_pct": 0,
                    "avg_lead_time_days": 0
                }
            }
        else:
            # Compute expected moves
            events = compute_weekly_events(conn, week_earnings, as_of_date)
            
            # Calculate summary statistics
            if events:
                valid_straddle = [e['em_straddle_pct'] for e in events if e['em_straddle_pct'] is not None]
                valid_iv = [e['em_iv_pct'] for e in events if e['em_iv_pct'] is not None]
                
                avg_em_straddle = sum(valid_straddle) / len(valid_straddle) if valid_straddle else 0
                avg_em_iv = sum(valid_iv) / len(valid_iv) if valid_iv else 0
                avg_lead_time = sum(e['lead_time_days'] for e in events) / len(events)
            else:
                avg_em_straddle = avg_em_iv = avg_lead_time = 0
            
            # Sort events by earnings date
            events.sort(key=lambda x: x['earnings_date'])
            
            # Generate output JSON
            output_data = {
                "metadata": {
                    "version": "v2_math_baseline_historical",
                    "generated_at": datetime.now().isoformat(),
                    "as_of_date": as_of_date.isoformat(),
                    "method": "math_baseline"
                },
                "window": {
                    "start": week_start.isoformat(),
                    "end": week_end.isoformat()
                },
                "events": events,
                "summary": {
                    "total_events": len(events),
                    "avg_em_straddle_pct": avg_em_straddle,
                    "avg_em_iv_pct": avg_em_iv,
                    "avg_lead_time_days": avg_lead_time
                }
            }
        
        # Write output
        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"✅ Generated {output_path} with {len(output_data['events'])} events")
        if output_data['events']:
            print(f"📊 Average straddle EM: {output_data['summary']['avg_em_straddle_pct']:.1%}")
            print(f"📊 Average IV EM: {output_data['summary']['avg_em_iv_pct']:.1%}")
            print(f"📅 Average lead time: T-{output_data['summary']['avg_lead_time_days']:.0f}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
