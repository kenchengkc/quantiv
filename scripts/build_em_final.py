#!/usr/bin/env python3
"""
Final expected move pipeline using only pandas and raw data.

Completely bypasses DuckDB views to avoid timezone issues.

Usage:
  python scripts/build_em_final.py --local
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

def load_all_data(data_dir):
    """Load all data using pandas directly."""
    print("[data] Loading all data with pandas...")
    
    # Load volatility data
    vol_files = glob.glob(str(data_dir / "parquet" / "volatility_history" / "**" / "*.parquet"), recursive=True)
    vol_dfs = []
    
    for file_path in vol_files:
        try:
            df = pd.read_parquet(file_path)
            if 'date' in df.columns and 'act_symbol' in df.columns and 'iv_current' in df.columns:
                df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
                df = df[df['iv_current'].notna() & (df['iv_current'] > 0)]
                vol_dfs.append(df)
        except Exception as e:
            continue
    
    vol_data = pd.concat(vol_dfs, ignore_index=True) if vol_dfs else pd.DataFrame()
    
    # Load options data
    opt_files = glob.glob(str(data_dir / "parquet" / "options_chain" / "**" / "*.parquet"), recursive=True)
    opt_dfs = []
    
    for file_path in opt_files:
        try:
            df = pd.read_parquet(file_path)
            if 'date' in df.columns and 'act_symbol' in df.columns and 'vol' in df.columns:
                df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
                df = df[df['vol'].notna() & (df['vol'] > 0) & df['call_put'].isin(['C', 'P'])]
                opt_dfs.append(df)
        except Exception as e:
            continue
    
    opt_data = pd.concat(opt_dfs, ignore_index=True) if opt_dfs else pd.DataFrame()
    
    print(f"[data] Loaded {len(vol_data):,} volatility rows, {len(opt_data):,} options rows")
    
    # Get unique symbols and date ranges
    if not vol_data.empty:
        vol_symbols = set(vol_data['act_symbol'].unique())
        vol_dates = pd.to_datetime(vol_data['date'])
        vol_date_range = (vol_dates.min(), vol_dates.max())
        print(f"[data] Volatility: {len(vol_symbols)} symbols, dates {vol_date_range[0].date()} to {vol_date_range[1].date()}")
    else:
        vol_symbols = set()
        vol_date_range = (None, None)
    
    if not opt_data.empty:
        opt_symbols = set(opt_data['act_symbol'].unique())
        opt_dates = pd.to_datetime(opt_data['date'])
        opt_date_range = (opt_dates.min(), opt_dates.max())
        print(f"[data] Options: {len(opt_symbols)} symbols, dates {opt_date_range[0].date()} to {opt_date_range[1].date()}")
    else:
        opt_symbols = set()
        opt_date_range = (None, None)
    
    return vol_data, opt_data, vol_symbols, opt_symbols, vol_date_range, opt_date_range

def create_synthetic_earnings(vol_symbols, vol_date_range):
    """Create synthetic earnings dates based on available data."""
    print("[earnings] Creating synthetic earnings dates...")
    
    if not vol_symbols or not vol_date_range[0]:
        print("[earnings] ⚠ No volatility data available")
        return pd.DataFrame()
    
    # Use middle of the volatility date range
    start_date = vol_date_range[0] + timedelta(days=5)
    end_date = vol_date_range[1] - timedelta(days=5)
    
    # Create earnings dates for top symbols
    common_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'NFLX']
    available_symbols = [s for s in common_symbols if s in vol_symbols]
    
    if not available_symbols:
        # Use any available symbols
        available_symbols = list(vol_symbols)[:10]
    
    print(f"[earnings] Using symbols: {available_symbols}")
    
    earnings_list = []
    current_date = start_date
    
    for i, symbol in enumerate(available_symbols):
        earnings_date = current_date + timedelta(days=i)
        if earnings_date <= end_date:
            earnings_list.append({
                'act_symbol': symbol,
                'earnings_date': earnings_date.date()
            })
    
    earnings_df = pd.DataFrame(earnings_list)
    print(f"[earnings] Created {len(earnings_df)} synthetic earnings dates")
    
    return earnings_df

def create_labels_and_features(vol_data, opt_data, earnings_df):
    """Create labels and features using pandas."""
    print("[pipeline] Creating labels and features...")
    
    if earnings_df.empty or vol_data.empty:
        print("[pipeline] ⚠ No earnings or volatility data")
        return pd.DataFrame(), pd.DataFrame()
    
    # Convert dates
    vol_data['trade_date'] = pd.to_datetime(vol_data['date'])
    earnings_df['earnings_date'] = pd.to_datetime(earnings_df['earnings_date'])
    
    if not opt_data.empty:
        opt_data['trade_date'] = pd.to_datetime(opt_data['date'])
        
        # Aggregate options data
        opt_agg = opt_data.groupby(['act_symbol', 'trade_date']).agg({
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
    else:
        opt_agg = pd.DataFrame()
    
    labels_list = []
    features_list = []
    
    for _, earning in earnings_df.iterrows():
        symbol = earning['act_symbol']
        earnings_date = earning['earnings_date']
        
        # Get symbol volatility data
        symbol_vol = vol_data[vol_data['act_symbol'] == symbol].copy()
        
        if len(symbol_vol) == 0:
            continue
        
        # Pre-earnings IV (1-7 days before)
        pre_start = earnings_date - timedelta(days=7)
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
        
        if len(pre_vol) >= 2 and len(post_vol) >= 1:
            iv_pre = pre_vol['iv_current'].mean()
            iv_post = post_vol['iv_current'].mean()
            
            if iv_pre > 0:
                proxy_move = abs(iv_post - iv_pre) / iv_pre
                signed_change = (iv_post - iv_pre) / iv_pre
                
                # Create label
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
                
                # Create features
                feature_row = {
                    'act_symbol': symbol,
                    'earnings_date': earnings_date.date(),
                    'y': proxy_move
                }
                
                # Add volatility features from most recent pre-earnings data
                latest_vol = pre_vol.sort_values('trade_date', ascending=False).iloc[0]
                feature_row.update({
                    'iv_t1': latest_vol['iv_current'],
                    'hv_t1': latest_vol.get('hv_current'),
                    'iv_week_ago': latest_vol.get('iv_week_ago'),
                    'iv_month_ago': latest_vol.get('iv_month_ago'),
                    'iv_hv_spread': (latest_vol['iv_current'] - latest_vol.get('hv_current', 0)) 
                                   if pd.notna(latest_vol.get('hv_current')) else None,
                    'iv_percentile_est': 0.5  # Placeholder
                })
                
                # Add options features if available
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
                            'call_put_ratio': 1.0,  # Placeholder
                            'total_contracts': latest_opt['total_contracts']
                        })
                
                features_list.append(feature_row)
    
    labels_df = pd.DataFrame(labels_list) if labels_list else pd.DataFrame()
    features_df = pd.DataFrame(features_list) if features_list else pd.DataFrame()
    
    print(f"[pipeline] Created {len(labels_df)} labels, {len(features_df)} features")
    
    return labels_df, features_df

def save_and_validate(conn, labels_df, features_df):
    """Save to DuckDB and validate."""
    print("[save] Saving to DuckDB...")
    
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
        
        # Validation
        print("[validation] Running validation...")
        if not labels_df.empty:
            labels_count = conn.execute("SELECT COUNT(*) FROM em_labels").fetchone()[0]
            print(f"  em_labels row count      : {labels_count}")
        
        if not features_df.empty:
            features_count = conn.execute("SELECT COUNT(*) FROM em_features").fetchone()[0]
            training_count = conn.execute("SELECT COUNT(*) FROM em_training").fetchone()[0]
            print(f"  em_features row count    : {features_count}")
            print(f"  em_training row count    : {training_count}")
            
            if training_count > 0:
                sample = conn.execute("SELECT act_symbol, earnings_date, y, iv_t1 FROM em_training LIMIT 3").fetchall()
                print("  Sample training data:")
                for row in sample:
                    print(f"    {row[0]}: {row[1]} -> y={row[2]:.3f}, iv={row[3]:.3f}")
        
        return True
        
    except Exception as e:
        print(f"[save] ⚠ Failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Final EM pipeline (pure pandas)")
    parser.add_argument("--local", action="store_true", help="Use local data directory")
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_dir = project_root / "data"
    else:
        data_dir = Path("/srv/quantiv-data")
    
    duckdb_path = data_dir / "quantiv.duckdb"
    
    print("[setup] Final EM pipeline (pure pandas)")
    print(f"[setup] Data dir: {data_dir}")
    print(f"[setup] DuckDB: {duckdb_path}")
    
    # Load all data
    vol_data, opt_data, vol_symbols, opt_symbols, vol_date_range, opt_date_range = load_all_data(data_dir)
    
    # Create synthetic earnings
    earnings_df = create_synthetic_earnings(vol_symbols, vol_date_range)
    
    # Create labels and features
    labels_df, features_df = create_labels_and_features(vol_data, opt_data, earnings_df)
    
    # Save and validate
    if duckdb_path.exists():
        conn = duckdb.connect(str(duckdb_path))
        success = save_and_validate(conn, labels_df, features_df)
        conn.close()
        
        if success and not labels_df.empty and not features_df.empty:
            print("\n✓ Final EM pipeline completed successfully!")
            print("\nNext steps:")
            print("1. Run baseline model training")
            print("2. Validate model performance")
        else:
            print("\n⚠ Pipeline completed but with limited data")
    else:
        print(f"\n⚠ DuckDB file not found: {duckdb_path}")

if __name__ == "__main__":
    main()
