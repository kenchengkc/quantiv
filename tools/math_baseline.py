#!/usr/bin/env python3
"""
Math baseline for earnings expected moves.
Implements ATM straddle % and IV_event calculations at any lead time T-k.
"""

import os
import sys
import duckdb
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional, Tuple
import math

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

def find_post_event_expiry(conn: duckdb.DuckDBPyConnection, ticker: str, as_of_date: date, earnings_dt: date) -> Optional[date]:
    """Find the nearest regular expiry after earnings (post-event window)."""
    
    # Look for expiries between earnings_dt and earnings_dt + 7 days
    max_expiry = earnings_dt + timedelta(days=7)
    
    result = conn.execute("""
        SELECT DISTINCT expiry_date
        FROM v_options_chain
        WHERE ticker = ?
        AND as_of_date = ?
        AND expiry_date >= ?
        AND expiry_date <= ?
        ORDER BY expiry_date
        LIMIT 1
    """, [ticker, as_of_date, earnings_dt, max_expiry]).fetchone()
    
    if result:
        return result[0]
    
    # Fallback: find nearest expiry after earnings (within 30 days)
    fallback_max = earnings_dt + timedelta(days=30)
    result = conn.execute("""
        SELECT DISTINCT expiry_date
        FROM v_options_chain
        WHERE ticker = ?
        AND as_of_date = ?
        AND expiry_date >= ?
        AND expiry_date <= ?
        ORDER BY expiry_date
        LIMIT 1
    """, [ticker, as_of_date, earnings_dt, fallback_max]).fetchone()
    
    return result[0] if result else None

def compute_atm_straddle_pct(conn: duckdb.DuckDBPyConnection, ticker: str, as_of_date: date, 
                            earnings_dt: date, expiry_date: date) -> Optional[Dict[str, Any]]:
    """Compute ATM straddle % for given ticker/date/expiry."""
    
    # Get ATM strike and spot estimate
    atm_info = conn.execute("""
        SELECT estimated_spot, atm_strike
        FROM v_atm_strikes
        WHERE ticker = ?
        AND as_of_date = ?
        AND expiry_date = ?
    """, [ticker, as_of_date, expiry_date]).fetchone()
    
    if not atm_info:
        return None
    
    estimated_spot, atm_strike = atm_info
    
    # Get call and put prices at ATM strike
    options_data = conn.execute("""
        SELECT 
            call_put,
            mid_price,
            iv,
            bid_ask_spread_pct,
            delta,
            vega
        FROM v_options_chain
        WHERE ticker = ?
        AND as_of_date = ?
        AND expiry_date = ?
        AND strike = ?
        AND call_put IN ('C', 'P')
    """, [ticker, as_of_date, expiry_date, atm_strike]).fetchall()
    
    if len(options_data) != 2:  # Need both call and put
        return None
    
    # Parse call and put data
    call_data = next((row for row in options_data if row[0] == 'C'), None)
    put_data = next((row for row in options_data if row[0] == 'P'), None)
    
    if not call_data or not put_data:
        return None
    
    call_mid, call_iv, call_spread, call_delta, call_vega = call_data[1:]
    put_mid, put_iv, put_spread, put_delta, put_vega = put_data[1:]
    
    # Calculate straddle metrics
    straddle_price = call_mid + put_mid
    straddle_pct = straddle_price / estimated_spot if estimated_spot > 0 else None
    
    # Average IV (vega-weighted if possible)
    if call_vega and put_vega and (call_vega + put_vega) > 0:
        avg_iv = (call_iv * call_vega + put_iv * put_vega) / (call_vega + put_vega)
    else:
        avg_iv = (call_iv + put_iv) / 2.0
    
    # Days to expiry
    days_to_expiry = (expiry_date - as_of_date).days
    
    # IV-based expected move (1-day equivalent)
    # EM = S * IV * sqrt(T/365) where T is days to expiry
    iv_em_pct = avg_iv * math.sqrt(days_to_expiry / 365.0) if days_to_expiry > 0 else None
    
    return {
        'ticker': ticker,
        'as_of_date': as_of_date.isoformat(),
        'earnings_dt': earnings_dt.isoformat(),
        'expiry_date': expiry_date.isoformat(),
        'days_to_expiry': days_to_expiry,
        'estimated_spot': float(estimated_spot),
        'atm_strike': float(atm_strike),
        'call_mid': float(call_mid),
        'put_mid': float(put_mid),
        'straddle_price': float(straddle_price),
        'straddle_pct': float(straddle_pct) if straddle_pct else None,
        'call_iv': float(call_iv),
        'put_iv': float(put_iv),
        'avg_iv': float(avg_iv),
        'iv_em_pct': float(iv_em_pct) if iv_em_pct else None,
        'call_delta': float(call_delta) if call_delta else None,
        'put_delta': float(put_delta) if put_delta else None,
        'total_vega': float(call_vega + put_vega) if call_vega and put_vega else None,
        'avg_bid_ask_spread': float((call_spread + put_spread) / 2.0) if call_spread and put_spread else None
    }

def compute_skew_and_term_structure(conn: duckdb.DuckDBPyConnection, ticker: str, as_of_date: date, 
                                   expiry_date: date, atm_strike: float) -> Dict[str, Any]:
    """Compute skew and term structure metrics."""
    
    # Get IV data around ATM for skew calculation
    skew_data = conn.execute("""
        SELECT 
            strike,
            call_put,
            iv,
            ABS(strike / ? - 1.0) as moneyness_distance
        FROM v_options_chain
        WHERE ticker = ?
        AND as_of_date = ?
        AND expiry_date = ?
        AND ABS(strike / ? - 1.0) <= 0.1  -- Within 10% moneyness
        ORDER BY moneyness_distance
    """, [atm_strike, ticker, as_of_date, expiry_date, atm_strike]).fetchall()
    
    # Calculate put-call skew (put IV - call IV at similar moneyness)
    put_ivs = [row[2] for row in skew_data if row[1] == 'P']
    call_ivs = [row[2] for row in skew_data if row[1] == 'C']
    
    skew_atm = None
    if put_ivs and call_ivs:
        skew_atm = np.mean(put_ivs) - np.mean(call_ivs)
    
    # Term structure: compare this expiry to longer-dated expiries
    term_data = conn.execute("""
        SELECT 
            expiry_date,
            AVG(iv) as avg_iv
        FROM v_options_chain
        WHERE ticker = ?
        AND as_of_date = ?
        AND expiry_date > ?
        AND expiry_date <= ?
        AND ABS(strike / ? - 1.0) <= 0.05  -- ATM strikes only
        GROUP BY expiry_date
        ORDER BY expiry_date
        LIMIT 3
    """, [ticker, as_of_date, expiry_date, as_of_date + timedelta(days=90), atm_strike]).fetchall()
    
    term_slope = None
    if len(term_data) >= 2:
        # Simple slope: (far_iv - near_iv) / days_diff
        near_iv = term_data[0][1]
        far_iv = term_data[-1][1]
        days_diff = (term_data[-1][0] - term_data[0][0]).days
        if days_diff > 0:
            term_slope = (far_iv - near_iv) / days_diff * 30  # Per 30 days
    
    return {
        'skew_atm': float(skew_atm) if skew_atm is not None else None,
        'term_slope': float(term_slope) if term_slope is not None else None,
        'num_skew_points': len(skew_data),
        'num_term_points': len(term_data)
    }

def compute_em_math(conn: duckdb.DuckDBPyConnection, ticker: str, as_of_date: date, 
                   earnings_dt: date) -> Optional[Dict[str, Any]]:
    """Compute math baseline EM for a ticker at given as_of_date (T-k)."""
    
    # Calculate lead time
    lead_time_days = (earnings_dt - as_of_date).days
    
    # Find post-event expiry
    expiry_date = find_post_event_expiry(conn, ticker, as_of_date, earnings_dt)
    if not expiry_date:
        return None
    
    # Compute ATM straddle %
    straddle_data = compute_atm_straddle_pct(conn, ticker, as_of_date, earnings_dt, expiry_date)
    if not straddle_data:
        return None
    
    # Compute skew and term structure
    skew_data = compute_skew_and_term_structure(conn, ticker, as_of_date, expiry_date, straddle_data['atm_strike'])
    
    # Combine all data
    result = {
        **straddle_data,
        'lead_time_days': lead_time_days,
        **skew_data,
        'em_baseline_straddle': straddle_data['straddle_pct'],
        'em_baseline_iv': straddle_data['iv_em_pct']
    }
    
    return result

def test_math_baseline(conn: duckdb.DuckDBPyConnection):
    """Test the math baseline with some sample data."""
    
    print("🧪 Testing math baseline calculations...")
    
    # Get a few recent earnings events with options data
    test_cases = conn.execute("""
        SELECT DISTINCT 
            e.ticker,
            e.earnings_dt,
            o.as_of_date
        FROM earnings_events e
        JOIN v_options_chain o ON e.ticker = o.ticker
        WHERE e.earnings_dt >= '2025-08-01'
        AND e.earnings_dt <= '2025-08-31'
        AND o.as_of_date <= e.earnings_dt
        AND o.as_of_date >= e.earnings_dt - INTERVAL '7 days'
        ORDER BY e.earnings_dt DESC, o.as_of_date DESC
        LIMIT 5
    """).fetchall()
    
    print(f"Found {len(test_cases)} test cases")
    
    for ticker, earnings_dt, as_of_date in test_cases:
        print(f"\nTesting {ticker} | Earnings: {earnings_dt} | As of: {as_of_date}")
        
        result = compute_em_math(conn, ticker, as_of_date, earnings_dt)
        
        if result:
            print(f"  ✅ Lead time: T-{result['lead_time_days']}")
            print(f"  📊 Straddle EM: {result['em_baseline_straddle']:.1%}")
            print(f"  📊 IV EM: {result['em_baseline_iv']:.1%}")
            print(f"  📈 ATM IV: {result['avg_iv']:.1%}")
            print(f"  📉 Skew: {result['skew_atm']:.3f}" if result['skew_atm'] else "  📉 Skew: N/A")
        else:
            print("  ❌ No data available")

def main():
    # Setup paths
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / "data"
    env_path = repo_root / "config" / ".env.local"
    
    # Load environment variables
    env_vars = load_env_file(env_path)
    
    print("🚀 Testing math baseline for earnings expected moves...")
    
    # Connect to DuckDB (in-memory, load views)
    conn = duckdb.connect()
    
    try:
        # First rebuild the views (since we're using in-memory)
        from build_earnings_events import build_earnings_events_table, create_duckdb_views
        
        earnings_csv = data_dir / "earnings_calendar.csv"
        build_earnings_events_table(conn, earnings_csv)
        create_duckdb_views(conn, data_dir)
        
        # Test the math baseline
        test_math_baseline(conn)
        
        print("\n✅ Math baseline testing complete!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
