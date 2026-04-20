#!/usr/bin/env python3
"""
Build canonical earnings_events table from earnings_calendar.csv
Creates the backbone for lead-time aware EM calculations.
"""

import os
import sys
import duckdb
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Dict, Any

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

def build_earnings_events_table(conn: duckdb.DuckDBPyConnection, earnings_csv: Path):
    """Build canonical earnings_events table from CSV."""
    
    print("📅 Building canonical earnings_events table...")
    
    # Create earnings_events table
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
            true as confirmed_flag,
            -- Add fiscal quarter placeholder (can be enhanced later)
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
    
    # Create index for fast lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_earnings_ticker_dt ON earnings_events(ticker, earnings_dt)")
    
    count = conn.execute("SELECT COUNT(*) FROM earnings_events").fetchone()[0]
    print(f"✅ Created earnings_events table with {count:,} events")
    
    # Show sample data
    sample = conn.execute("""
        SELECT ticker, earnings_dt, timing, fiscal_q 
        FROM earnings_events 
        ORDER BY earnings_dt DESC 
        LIMIT 5
    """).fetchall()
    
    print("Sample events:")
    for row in sample:
        print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]}")

def create_duckdb_views(conn: duckdb.DuckDBPyConnection, data_dir: Path):
    """Create DuckDB views for options_chain and volatility_history."""
    
    print("🦆 Creating DuckDB views...")
    
    # Options chain view (dedup on ticker/date/expiry/strike/call_put since parquet files can overlap)
    options_pattern = str(data_dir / "parquet" / "options_chain" / "*" / "*" / "*.parquet")
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_options_chain AS
        SELECT * FROM (
        SELECT
            act_symbol as ticker,
            CAST(date AS DATE) as as_of_date,
            CAST(expiration AS DATE) as expiry_date,
            strike,
            CASE
                WHEN UPPER(call_put) IN ('C', 'CALL') THEN 'C'
                WHEN UPPER(call_put) IN ('P', 'PUT') THEN 'P'
                ELSE NULL
            END as call_put,
            bid,
            ask,
            (bid + ask) / 2.0 as mid_price,
            CASE WHEN bid > 0 AND ask > 0 THEN (ask - bid) / ((ask + bid) / 2.0) ELSE NULL END as bid_ask_spread_pct,
            delta,
            gamma,
            theta,
            vega,
            vol as iv
        FROM read_parquet('{options_pattern}')
        WHERE bid IS NOT NULL 
        AND ask IS NOT NULL
        AND bid > 0
        AND ask > bid
        AND vol IS NOT NULL
        AND vol > 0
        AND vol < 5.0  -- Filter extreme IVs
        QUALIFY ROW_NUMBER() OVER (PARTITION BY act_symbol, date, expiration, strike, call_put ORDER BY (bid+ask) DESC) = 1
        )
    """)
    
    # Volatility history view  
    vol_pattern = str(data_dir / "parquet" / "volatility_history" / "*" / "*" / "*.parquet")
    if Path(vol_pattern.replace("*/*/*", "")).exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_volatility_history AS
            SELECT 
                ticker,
                CAST(date AS DATE) as as_of_date,
                close_price,
                realized_vol_5d,
                realized_vol_10d,
                realized_vol_20d,
                realized_vol_30d,
                realized_vol_60d,
                atr_14
            FROM read_parquet('{vol_pattern}')
            WHERE close_price IS NOT NULL
            AND close_price > 0
        """)
        print("✅ Created v_volatility_history view")
    else:
        print("⚠️  Volatility history parquet files not found, skipping v_volatility_history view")
    
    # ATM helper view - finds ATM strikes for each ticker/date/expiry combination
    conn.execute("""
        CREATE OR REPLACE VIEW v_atm_strikes AS
        WITH spot_estimates AS (
            -- Estimate spot price from options data (use call delta closest to 0.5)
            SELECT 
                ticker,
                as_of_date,
                expiry_date,
                strike as estimated_spot,
                ROW_NUMBER() OVER (PARTITION BY ticker, as_of_date, expiry_date ORDER BY ABS(delta - 0.5)) as rn
            FROM v_options_chain
            WHERE call_put = 'C'
            AND delta IS NOT NULL
            AND delta BETWEEN 0.3 AND 0.7
        ),
        atm_candidates AS (
            SELECT 
                o.ticker,
                o.as_of_date,
                o.expiry_date,
                s.estimated_spot,
                o.strike,
                ABS(o.strike - s.estimated_spot) as strike_distance,
                ROW_NUMBER() OVER (PARTITION BY o.ticker, o.as_of_date, o.expiry_date ORDER BY ABS(o.strike - s.estimated_spot)) as rn
            FROM v_options_chain o
            JOIN spot_estimates s ON o.ticker = s.ticker 
                AND o.as_of_date = s.as_of_date 
                AND o.expiry_date = s.expiry_date
                AND s.rn = 1
        )
        SELECT 
            ticker,
            as_of_date,
            expiry_date,
            estimated_spot,
            strike as atm_strike,
            strike_distance
        FROM atm_candidates
        WHERE rn = 1
    """)
    
    print("✅ Created v_options_chain and v_atm_strikes views")
    
    # Test the views
    options_count = conn.execute("SELECT COUNT(*) FROM v_options_chain").fetchone()[0]
    atm_count = conn.execute("SELECT COUNT(*) FROM v_atm_strikes").fetchone()[0]
    
    print(f"📊 v_options_chain: {options_count:,} records")
    print(f"📊 v_atm_strikes: {atm_count:,} records")

def main():
    # Setup paths
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / "data"
    earnings_csv = data_dir / "earnings_calendar.csv"
    duckdb_path = data_dir / "quantiv.duckdb"
    env_path = repo_root / "config" / ".env.local"
    
    # Load environment variables
    env_vars = load_env_file(env_path)
    
    print("🚀 Building earnings events and DuckDB views...")
    
    # Check required files
    if not earnings_csv.exists():
        print(f"❌ Earnings calendar not found: {earnings_csv}")
        sys.exit(1)
    
    # Connect to DuckDB (use in-memory to avoid lock conflicts)
    conn = duckdb.connect()
    
    try:
        # Build earnings events table
        build_earnings_events_table(conn, earnings_csv)
        
        # Create DuckDB views
        create_duckdb_views(conn, data_dir)
        
        print("✅ Successfully built earnings events and views!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
