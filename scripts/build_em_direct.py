#!/usr/bin/env python3
"""
Direct expected move labels and features builder.

Bypasses DuckDB views and queries Parquet files directly to avoid timezone issues.

Usage:
  python scripts/build_em_direct.py --local
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def create_direct_labels(conn, data_dir):
    """Create labels by querying Parquet files directly."""
    print("[labels] Creating em_labels from direct Parquet queries...")
    
    try:
        # Get earnings data
        earnings_df = pd.read_sql_query("""
            SELECT act_symbol, earnings_date 
            FROM v_earnings 
            WHERE earnings_date BETWEEN '2024-01-01' AND '2025-12-31'
        """, conn)
        
        # Query volatility Parquet files directly
        parquet_vol_path = data_dir / "parquet" / "volatility_history" / "**" / "*.parquet"
        vol_df = pd.read_sql_query(f"""
            SELECT act_symbol, 
                   date::DATE as trade_date,
                   iv_current
            FROM read_parquet('{parquet_vol_path}')
            WHERE iv_current IS NOT NULL 
              AND iv_current > 0
              AND date IS NOT NULL
        """, conn)
        
        # Convert date columns
        earnings_df['earnings_date'] = pd.to_datetime(earnings_df['earnings_date'])
        vol_df['trade_date'] = pd.to_datetime(vol_df['trade_date'])
        
        labels_list = []
        
        for _, earning in earnings_df.iterrows():
            symbol = earning['act_symbol']
            earnings_date = earning['earnings_date']
            
            # Get symbol volatility data
            symbol_vol = vol_df[vol_df['act_symbol'] == symbol].copy()
            
            if len(symbol_vol) == 0:
                continue
                
            # Pre-earnings IV (1-10 days before)
            pre_start = earnings_date - timedelta(days=10)
            pre_end = earnings_date - timedelta(days=1)
            pre_vol = symbol_vol[
                (symbol_vol['trade_date'] >= pre_start) & 
                (symbol_vol['trade_date'] <= pre_end)
            ]
            
            # Post-earnings IV (1-5 days after)
            post_start = earnings_date + timedelta(days=1)
            post_end = earnings_date + timedelta(days=5)
            post_vol = symbol_vol[
                (symbol_vol['trade_date'] >= post_start) & 
                (symbol_vol['trade_date'] <= post_end)
            ]
            
            if len(pre_vol) >= 3 and len(post_vol) >= 2:
                iv_pre = pre_vol['iv_current'].mean()
                iv_post = post_vol['iv_current'].mean()
                
                if iv_pre > 0:
                    proxy_move = abs(iv_post - iv_pre) / iv_pre
                    signed_change = (iv_post - iv_pre) / iv_pre
                    
                    labels_list.append({
                        'act_symbol': symbol,
                        'earnings_date': earnings_date.date(),
                        'iv_pre': iv_pre,
                        'iv_post': iv_post,
                        'pre_days': len(pre_vol),
                        'post_days': len(post_vol),
                        'proxy_realized_move': proxy_move,
                        'signed_iv_change': signed_change,
                        'created_at': datetime.now()
                    })
        
        # Create labels DataFrame and save to DuckDB
        if labels_list:
            labels_df = pd.DataFrame(labels_list)
            
            # Drop existing table and create new one
            conn.execute("DROP TABLE IF EXISTS em_labels")
            
            # Register DataFrame with DuckDB and create table
            conn.register('labels_temp', labels_df)
            conn.execute("""
                CREATE TABLE em_labels AS 
                SELECT * FROM labels_temp
            """)
            conn.unregister('labels_temp')
            
            print(f"[labels] ✓ Created em_labels table with {len(labels_df)} rows")
            return True
        else:
            print("[labels] ⚠ No valid labels created")
            return False
            
    except Exception as e:
        print(f"[labels] ⚠ Failed to create em_labels: {e}")
        return False

def create_direct_features(conn, data_dir):
    """Create features by querying Parquet files directly."""
    print("[features] Creating em_features from direct Parquet queries...")
    
    try:
        # Get labels
        labels_df = pd.read_sql_query("SELECT * FROM em_labels", conn)
        
        # Query Parquet files directly
        parquet_vol_path = data_dir / "parquet" / "volatility_history" / "**" / "*.parquet"
        parquet_opt_path = data_dir / "parquet" / "options_chain" / "**" / "*.parquet"
        
        # Get volatility features directly from Parquet
        vol_features = pd.read_sql_query(f"""
            SELECT act_symbol,
                   date::DATE as trade_date,
                   iv_current,
                   hv_current,
                   iv_week_ago,
                   iv_month_ago,
                   iv_year_high,
                   iv_year_low
            FROM read_parquet('{parquet_vol_path}')
            WHERE iv_current IS NOT NULL
              AND date IS NOT NULL
        """, conn)
        
        # Get options features directly from Parquet
        opt_features = pd.read_sql_query(f"""
            SELECT act_symbol,
                   date::DATE as trade_date,
                   AVG(vol) as avg_iv_t1,
                   AVG(CASE WHEN call_put = 'C' THEN vol END) as call_iv,
                   AVG(CASE WHEN call_put = 'P' THEN vol END) as put_iv,
                   AVG(gamma) as avg_gamma_t1,
                   AVG(vega) as avg_vega_t1,
                   COUNT(*) as total_contracts,
                   SUM(CASE WHEN call_put = 'C' THEN 1 ELSE 0 END) / 
                   NULLIF(SUM(CASE WHEN call_put = 'P' THEN 1 ELSE 0 END), 0) as call_put_ratio
            FROM read_parquet('{parquet_opt_path}')
            WHERE vol IS NOT NULL 
              AND vol > 0
              AND date IS NOT NULL
              AND call_put IN ('C', 'P')
            GROUP BY act_symbol, date::DATE
        """, conn)
        
        # Convert dates
        labels_df['earnings_date'] = pd.to_datetime(labels_df['earnings_date'])
        vol_features['trade_date'] = pd.to_datetime(vol_features['trade_date'])
        opt_features['trade_date'] = pd.to_datetime(opt_features['trade_date'])
        
        features_list = []
        
        for _, label in labels_df.iterrows():
            symbol = label['act_symbol']
            earnings_date = label['earnings_date']
            
            # Get most recent volatility data before earnings
            symbol_vol = vol_features[vol_features['act_symbol'] == symbol]
            pre_vol = symbol_vol[
                (symbol_vol['trade_date'] <= earnings_date - timedelta(days=1)) &
                (symbol_vol['trade_date'] >= earnings_date - timedelta(days=10))
            ].sort_values('trade_date', ascending=False)
            
            # Get most recent options data before earnings
            symbol_opt = opt_features[opt_features['act_symbol'] == symbol]
            pre_opt = symbol_opt[
                (symbol_opt['trade_date'] <= earnings_date - timedelta(days=1)) &
                (symbol_opt['trade_date'] >= earnings_date - timedelta(days=5))
            ].sort_values('trade_date', ascending=False)
            
            # Build feature row
            feature_row = {
                'act_symbol': symbol,
                'earnings_date': earnings_date.date(),
                'y': label['proxy_realized_move']
            }
            
            # Add volatility features
            if len(pre_vol) > 0:
                latest_vol = pre_vol.iloc[0]
                feature_row.update({
                    'iv_t1': latest_vol['iv_current'],
                    'hv_t1': latest_vol['hv_current'],
                    'iv_week_ago': latest_vol['iv_week_ago'],
                    'iv_month_ago': latest_vol['iv_month_ago'],
                    'iv_hv_spread': latest_vol['iv_current'] - latest_vol['hv_current'] if pd.notna(latest_vol['hv_current']) else None,
                    'iv_percentile_est': (latest_vol['iv_current'] - latest_vol['iv_year_low']) / 
                                       (latest_vol['iv_year_high'] - latest_vol['iv_year_low']) 
                                       if pd.notna(latest_vol['iv_year_high']) and pd.notna(latest_vol['iv_year_low']) 
                                       and latest_vol['iv_year_high'] > latest_vol['iv_year_low'] else None
                })
            
            # Add options features
            if len(pre_opt) > 0:
                latest_opt = pre_opt.iloc[0]
                feature_row.update({
                    'avg_iv_t1': latest_opt['avg_iv_t1'],
                    'atm_iv_t1': latest_opt['avg_iv_t1'],  # Simplified - use average as ATM proxy
                    'iv_skew': latest_opt['call_iv'] - latest_opt['put_iv'] if pd.notna(latest_opt['call_iv']) and pd.notna(latest_opt['put_iv']) else None,
                    'avg_gamma_t1': latest_opt['avg_gamma_t1'],
                    'avg_vega_t1': latest_opt['avg_vega_t1'],
                    'call_put_ratio': latest_opt['call_put_ratio'],
                    'total_contracts': latest_opt['total_contracts']
                })
            
            features_list.append(feature_row)
        
        # Create features DataFrame and save
        if features_list:
            features_df = pd.DataFrame(features_list)
            
            conn.execute("DROP TABLE IF EXISTS em_features")
            conn.register('features_temp', features_df)
            conn.execute("CREATE TABLE em_features AS SELECT * FROM features_temp")
            conn.unregister('features_temp')
            
            print(f"[features] ✓ Created em_features table with {len(features_df)} rows")
            return True
        else:
            print("[features] ⚠ No features created")
            return False
            
    except Exception as e:
        print(f"[features] ⚠ Failed to create em_features: {e}")
        return False

def create_training_view(conn):
    """Create training view joining labels and features."""
    print("[training] Creating em_training view...")
    
    try:
        conn.execute("""
            CREATE OR REPLACE VIEW em_training AS
            SELECT 
                f.act_symbol,
                f.earnings_date,
                f.y,
                f.iv_t1,
                f.hv_t1,
                f.iv_week_ago,
                f.iv_month_ago,
                f.iv_hv_spread,
                f.iv_percentile_est,
                f.avg_iv_t1,
                f.atm_iv_t1,
                f.iv_skew,
                f.avg_gamma_t1,
                f.avg_vega_t1,
                f.call_put_ratio,
                f.total_contracts
            FROM em_features f
            WHERE f.y IS NOT NULL
              AND f.y > 0
              AND f.iv_t1 IS NOT NULL
        """)
        
        print("[training] ✓ Created em_training view")
        return True
        
    except Exception as e:
        print(f"[training] ⚠ Failed to create em_training view: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Build EM labels and features (direct Parquet)")
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
    
    print("[setup] Building EM labels and features (direct Parquet)")
    print(f"[setup] Data dir: {data_dir}")
    print(f"[setup] DuckDB: {duckdb_path}")
    print(f"[setup] Timestamp: {datetime.now()}")
    
    if not duckdb_path.exists():
        print(f"[error] DuckDB file not found: {duckdb_path}")
        sys.exit(1)
    
    # Connect to DuckDB
    conn = duckdb.connect(str(duckdb_path))
    
    success_count = 0
    total_steps = 3
    
    # Step 1: Create labels
    if create_direct_labels(conn, data_dir):
        success_count += 1
    
    # Step 2: Create features
    if create_direct_features(conn, data_dir):
        success_count += 1
    
    # Step 3: Create training view
    if create_training_view(conn):
        success_count += 1
    
    # Validation
    print("[validation] Running validation checks...")
    try:
        labels_count = conn.execute("SELECT COUNT(*) FROM em_labels").fetchone()[0]
        features_count = conn.execute("SELECT COUNT(*) FROM em_features").fetchone()[0]
        training_count = conn.execute("SELECT COUNT(*) FROM em_training").fetchone()[0]
        
        print(f"  em_labels row count      : {labels_count}")
        print(f"  em_features row count    : {features_count}")
        print(f"  em_training row count    : {training_count}")
        
        if training_count > 0:
            iv_data_count = conn.execute("SELECT COUNT(*) FROM em_training WHERE iv_t1 IS NOT NULL").fetchone()[0]
            print(f"  Training rows with IV    : {iv_data_count}")
            
            # Sample of training data
            sample = conn.execute("SELECT act_symbol, earnings_date, y, iv_t1 FROM em_training LIMIT 5").fetchall()
            print(f"  Sample training data:")
            for row in sample:
                print(f"    {row[0]}: {row[1]} -> y={row[2]:.3f}, iv={row[3]:.3f}")
            
    except Exception as e:
        print(f"  Validation error: {e}")
    
    conn.close()
    
    print(f"\n[success] Direct Parquet EM pipeline completed ({success_count}/{total_steps} steps)")
    
    if success_count == total_steps:
        print("✓ All steps completed successfully!")
        print("\nNext steps:")
        print("1. Run baseline model training")
        print("2. Validate model performance")
    else:
        print(f"⚠ {total_steps - success_count} steps failed")

if __name__ == "__main__":
    main()
