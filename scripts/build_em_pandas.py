#!/usr/bin/env python3
"""
Pure pandas expected move labels and features builder.

Uses pandas to read Parquet files directly and create ML pipeline tables,
completely bypassing DuckDB timezone conversion issues.

Usage:
  python scripts/build_em_pandas.py --local
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

def load_earnings_data(conn):
    """Load earnings calendar from DuckDB."""
    try:
        return pd.read_sql_query("""
            SELECT act_symbol, earnings_date 
            FROM v_earnings 
            WHERE earnings_date BETWEEN '2024-01-01' AND '2025-12-31'
        """, conn)
    except Exception as e:
        print(f"[error] Failed to load earnings data: {e}")
        return pd.DataFrame()

def load_parquet_data(data_dir):
    """Load volatility and options data from Parquet files using pandas."""
    print("[data] Loading Parquet files with pandas...")
    
    # Load volatility history
    vol_files = glob.glob(str(data_dir / "parquet" / "volatility_history" / "**" / "*.parquet"), recursive=True)
    vol_dfs = []
    
    for file_path in vol_files[:10]:  # Limit to first 10 files for testing
        try:
            df = pd.read_parquet(file_path)
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
            vol_dfs.append(df)
        except Exception as e:
            print(f"[warning] Failed to read {file_path}: {e}")
    
    vol_data = pd.concat(vol_dfs, ignore_index=True) if vol_dfs else pd.DataFrame()
    
    # Load options chain
    opt_files = glob.glob(str(data_dir / "parquet" / "options_chain" / "**" / "*.parquet"), recursive=True)
    opt_dfs = []
    
    for file_path in opt_files[:10]:  # Limit to first 10 files for testing
        try:
            df = pd.read_parquet(file_path)
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
            opt_dfs.append(df)
        except Exception as e:
            print(f"[warning] Failed to read {file_path}: {e}")
    
    opt_data = pd.concat(opt_dfs, ignore_index=True) if opt_dfs else pd.DataFrame()
    
    print(f"[data] Loaded {len(vol_data):,} volatility rows, {len(opt_data):,} options rows")
    return vol_data, opt_data

def create_pandas_labels(earnings_df, vol_data):
    """Create labels using pandas operations."""
    print("[labels] Creating em_labels with pandas...")
    
    if vol_data.empty or earnings_df.empty:
        print("[labels] ⚠ No data available for label creation")
        return pd.DataFrame()
    
    # Ensure date columns are datetime
    earnings_df['earnings_date'] = pd.to_datetime(earnings_df['earnings_date'])
    vol_data['trade_date'] = pd.to_datetime(vol_data['date'])
    
    # Filter valid volatility data
    vol_clean = vol_data[
        (vol_data['iv_current'].notna()) & 
        (vol_data['iv_current'] > 0) &
        (vol_data['act_symbol'].notna())
    ].copy()
    
    labels_list = []
    
    for _, earning in earnings_df.iterrows():
        symbol = earning['act_symbol']
        earnings_date = earning['earnings_date']
        
        # Get symbol volatility data
        symbol_vol = vol_clean[vol_clean['act_symbol'] == symbol].copy()
        
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
    
    if labels_list:
        labels_df = pd.DataFrame(labels_list)
        print(f"[labels] ✓ Created {len(labels_df)} labels")
        return labels_df
    else:
        print("[labels] ⚠ No valid labels created")
        return pd.DataFrame()

def create_pandas_features(labels_df, vol_data, opt_data):
    """Create features using pandas operations."""
    print("[features] Creating em_features with pandas...")
    
    if labels_df.empty:
        print("[features] ⚠ No labels available for feature creation")
        return pd.DataFrame()
    
    # Prepare data
    labels_df['earnings_date'] = pd.to_datetime(labels_df['earnings_date'])
    
    if not vol_data.empty:
        vol_data['trade_date'] = pd.to_datetime(vol_data['date'])
        vol_clean = vol_data[vol_data['iv_current'].notna()].copy()
    else:
        vol_clean = pd.DataFrame()
    
    if not opt_data.empty:
        opt_data['trade_date'] = pd.to_datetime(opt_data['date'])
        opt_clean = opt_data[
            (opt_data['vol'].notna()) & 
            (opt_data['vol'] > 0) &
            (opt_data['call_put'].isin(['C', 'P']))
        ].copy()
        
        # Aggregate options by symbol and date
        opt_agg = opt_clean.groupby(['act_symbol', 'trade_date']).agg({
            'vol': 'mean',
            'gamma': 'mean',
            'vega': 'mean',
            'call_put': 'count'
        }).rename(columns={
            'vol': 'avg_iv_t1',
            'gamma': 'avg_gamma_t1',
            'vega': 'avg_vega_t1',
            'call_put': 'total_contracts'
        }).reset_index()
        
        # Calculate call/put ratio
        call_counts = opt_clean[opt_clean['call_put'] == 'C'].groupby(['act_symbol', 'trade_date']).size()
        put_counts = opt_clean[opt_clean['call_put'] == 'P'].groupby(['act_symbol', 'trade_date']).size()
        
        opt_agg = opt_agg.set_index(['act_symbol', 'trade_date'])
        opt_agg['call_put_ratio'] = call_counts / put_counts.replace(0, np.nan)
        opt_agg = opt_agg.reset_index()
    else:
        opt_agg = pd.DataFrame()
    
    features_list = []
    
    for _, label in labels_df.iterrows():
        symbol = label['act_symbol']
        earnings_date = label['earnings_date']
        
        # Build feature row
        feature_row = {
            'act_symbol': symbol,
            'earnings_date': earnings_date.date(),
            'y': label['proxy_realized_move']
        }
        
        # Add volatility features
        if not vol_clean.empty:
            symbol_vol = vol_clean[vol_clean['act_symbol'] == symbol]
            pre_vol = symbol_vol[
                (symbol_vol['trade_date'] <= earnings_date - timedelta(days=1)) &
                (symbol_vol['trade_date'] >= earnings_date - timedelta(days=10))
            ].sort_values('trade_date', ascending=False)
            
            if len(pre_vol) > 0:
                latest_vol = pre_vol.iloc[0]
                feature_row.update({
                    'iv_t1': latest_vol['iv_current'],
                    'hv_t1': latest_vol.get('hv_current'),
                    'iv_week_ago': latest_vol.get('iv_week_ago'),
                    'iv_month_ago': latest_vol.get('iv_month_ago'),
                    'iv_hv_spread': (latest_vol['iv_current'] - latest_vol.get('hv_current', 0)) 
                                   if pd.notna(latest_vol.get('hv_current')) else None,
                    'iv_percentile_est': 0.5  # Placeholder
                })
        
        # Add options features
        if not opt_agg.empty:
            symbol_opt = opt_agg[opt_agg['act_symbol'] == symbol]
            pre_opt = symbol_opt[
                (symbol_opt['trade_date'] <= earnings_date - timedelta(days=1)) &
                (symbol_opt['trade_date'] >= earnings_date - timedelta(days=5))
            ].sort_values('trade_date', ascending=False)
            
            if len(pre_opt) > 0:
                latest_opt = pre_opt.iloc[0]
                feature_row.update({
                    'avg_iv_t1': latest_opt['avg_iv_t1'],
                    'atm_iv_t1': latest_opt['avg_iv_t1'],
                    'iv_skew': 0.0,  # Placeholder
                    'avg_gamma_t1': latest_opt['avg_gamma_t1'],
                    'avg_vega_t1': latest_opt['avg_vega_t1'],
                    'call_put_ratio': latest_opt['call_put_ratio'],
                    'total_contracts': latest_opt['total_contracts']
                })
        
        features_list.append(feature_row)
    
    if features_list:
        features_df = pd.DataFrame(features_list)
        print(f"[features] ✓ Created {len(features_df)} feature rows")
        return features_df
    else:
        print("[features] ⚠ No features created")
        return pd.DataFrame()

def save_to_duckdb(conn, labels_df, features_df):
    """Save DataFrames to DuckDB tables."""
    print("[save] Saving tables to DuckDB...")
    
    try:
        # Save labels
        if not labels_df.empty:
            conn.execute("DROP TABLE IF EXISTS em_labels")
            conn.register('labels_temp', labels_df)
            conn.execute("CREATE TABLE em_labels AS SELECT * FROM labels_temp")
            conn.unregister('labels_temp')
            print(f"[save] ✓ Saved em_labels ({len(labels_df)} rows)")
        
        # Save features
        if not features_df.empty:
            conn.execute("DROP TABLE IF EXISTS em_features")
            conn.register('features_temp', features_df)
            conn.execute("CREATE TABLE em_features AS SELECT * FROM features_temp")
            conn.unregister('features_temp')
            print(f"[save] ✓ Saved em_features ({len(features_df)} rows)")
        
        # Create training view
        if not features_df.empty:
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
            print("[save] ✓ Created em_training view")
        
        return True
        
    except Exception as e:
        print(f"[save] ⚠ Failed to save to DuckDB: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Build EM labels and features (pure pandas)")
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
    
    print("[setup] Building EM labels and features (pure pandas)")
    print(f"[setup] Data dir: {data_dir}")
    print(f"[setup] DuckDB: {duckdb_path}")
    print(f"[setup] Timestamp: {datetime.now()}")
    
    if not duckdb_path.exists():
        print(f"[error] DuckDB file not found: {duckdb_path}")
        sys.exit(1)
    
    # Connect to DuckDB
    conn = duckdb.connect(str(duckdb_path))
    
    # Load data
    earnings_df = load_earnings_data(conn)
    vol_data, opt_data = load_parquet_data(data_dir)
    
    # Create labels and features
    labels_df = create_pandas_labels(earnings_df, vol_data)
    features_df = create_pandas_features(labels_df, vol_data, opt_data)
    
    # Save to DuckDB
    success = save_to_duckdb(conn, labels_df, features_df)
    
    # Validation
    if success:
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
    
    if success and not labels_df.empty and not features_df.empty:
        print("\n✓ Pure pandas EM pipeline completed successfully!")
        print("\nNext steps:")
        print("1. Run baseline model training")
        print("2. Validate model performance")
    else:
        print("\n⚠ Pipeline failed or produced no data")

if __name__ == "__main__":
    main()
