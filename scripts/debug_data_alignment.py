#!/usr/bin/env python3
"""
Debug script to check data alignment between earnings dates and available data.
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import glob

def debug_data_alignment(data_dir, duckdb_path):
    """Debug earnings vs data date alignment."""
    print("[debug] Checking data alignment...")
    
    # Connect to DuckDB
    conn = duckdb.connect(str(duckdb_path))
    
    # Get earnings data
    try:
        earnings_df = pd.read_sql_query("""
            SELECT act_symbol, earnings_date 
            FROM v_earnings 
            WHERE earnings_date BETWEEN '2024-01-01' AND '2025-12-31'
        """, conn)
        print(f"[debug] Loaded {len(earnings_df)} earnings dates")
        print(f"[debug] Earnings date range: {earnings_df['earnings_date'].min()} to {earnings_df['earnings_date'].max()}")
        print(f"[debug] Sample earnings:")
        for _, row in earnings_df.head().iterrows():
            print(f"  {row['act_symbol']}: {row['earnings_date']}")
    except Exception as e:
        print(f"[debug] Failed to load earnings: {e}")
        return
    
    # Load sample volatility data
    vol_files = glob.glob(str(data_dir / "parquet" / "volatility_history" / "**" / "*.parquet"), recursive=True)
    print(f"[debug] Found {len(vol_files)} volatility files")
    
    if vol_files:
        sample_vol = pd.read_parquet(vol_files[0])
        print(f"[debug] Sample volatility file columns: {list(sample_vol.columns)}")
        print(f"[debug] Sample volatility dates:")
        if 'date' in sample_vol.columns:
            sample_vol['date'] = pd.to_datetime(sample_vol['date'], errors='coerce')
            print(f"  Date range: {sample_vol['date'].min()} to {sample_vol['date'].max()}")
            print(f"  Sample symbols: {sample_vol['act_symbol'].unique()[:5]}")
        
        # Check for earnings symbols in volatility data
        earnings_symbols = set(earnings_df['act_symbol'].unique())
        vol_symbols = set(sample_vol['act_symbol'].unique())
        common_symbols = earnings_symbols.intersection(vol_symbols)
        print(f"[debug] Common symbols between earnings and volatility: {len(common_symbols)}")
        print(f"[debug] Sample common symbols: {list(common_symbols)[:5]}")
    
    # Load sample options data
    opt_files = glob.glob(str(data_dir / "parquet" / "options_chain" / "**" / "*.parquet"), recursive=True)
    print(f"[debug] Found {len(opt_files)} options files")
    
    if opt_files:
        sample_opt = pd.read_parquet(opt_files[0])
        print(f"[debug] Sample options file columns: {list(sample_opt.columns)}")
        if 'date' in sample_opt.columns:
            sample_opt['date'] = pd.to_datetime(sample_opt['date'], errors='coerce')
            print(f"  Date range: {sample_opt['date'].min()} to {sample_opt['date'].max()}")
    
    # Check specific symbol alignment
    if earnings_df.empty:
        return
        
    test_symbol = earnings_df.iloc[0]['act_symbol']
    test_earnings_date = pd.to_datetime(earnings_df.iloc[0]['earnings_date'])
    
    print(f"\n[debug] Testing alignment for {test_symbol} on {test_earnings_date}")
    
    # Check volatility data around earnings date
    if vol_files:
        vol_data_list = []
        for file_path in vol_files[:5]:  # Check first 5 files
            try:
                df = pd.read_parquet(file_path)
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                symbol_data = df[df['act_symbol'] == test_symbol]
                if not symbol_data.empty:
                    vol_data_list.append(symbol_data)
            except Exception as e:
                continue
        
        if vol_data_list:
            vol_combined = pd.concat(vol_data_list, ignore_index=True)
            
            # Check data around earnings date
            window_start = test_earnings_date - timedelta(days=15)
            window_end = test_earnings_date + timedelta(days=15)
            
            window_data = vol_combined[
                (vol_combined['date'] >= window_start) & 
                (vol_combined['date'] <= window_end)
            ].sort_values('date')
            
            print(f"[debug] Volatility data for {test_symbol} around {test_earnings_date}:")
            print(f"  Found {len(window_data)} rows in ±15 day window")
            if not window_data.empty:
                for _, row in window_data.head(10).iterrows():
                    print(f"    {row['date']}: IV={row.get('iv_current', 'N/A')}")
    
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="Debug data alignment")
    parser.add_argument("--local", action="store_true", help="Use local data directory")
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_dir = project_root / "data"
        duckdb_path = data_dir / "quantiv.duckdb"
    else:
        data_dir = Path("/srv/quantiv-data")
        duckdb_path = data_dir / "quantiv.duckdb"
    
    debug_data_alignment(data_dir, duckdb_path)

if __name__ == "__main__":
    main()
