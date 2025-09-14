#!/usr/bin/env python3
"""
Populate DuckDB with ML forecasts for frontend integration.
Creates em_forecasts table with predictions from trained models.
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
import pickle
import json

# Add backend services to path
sys.path.append(str(Path(__file__).parent.parent / "apps" / "backend"))
from services.ml_service import MLService

def create_forecasts_table(conn, data_dir):
    """Create em_forecasts table structure."""
    print("[setup] Creating em_forecasts table...")
    
    conn.execute("""
        DROP TABLE IF EXISTS em_forecasts
    """)
    
    conn.execute("""
        CREATE TABLE em_forecasts (
            underlying VARCHAR,
            quote_ts TIMESTAMP,
            exp_date DATE,
            horizon VARCHAR,
            em_baseline DOUBLE,
            band68_low DOUBLE,
            band68_high DOUBLE,
            band95_low DOUBLE,
            band95_high DOUBLE,
            model_type VARCHAR,
            confidence VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    print("[setup] ✓ Created em_forecasts table")

def generate_ml_forecasts(ml_service, symbols, horizon_days=[1, 5, 30]):
    """Generate ML forecasts for multiple horizons."""
    print(f"[ml] Generating forecasts for {len(symbols)} symbols...")
    
    forecasts = []
    current_time = datetime.now()
    
    for i, symbol in enumerate(symbols):
        if i % 10 == 0:
            print(f"[ml] Processing {i+1}/{len(symbols)} symbols...")
        
        try:
            # Get base prediction
            prediction = ml_service.predict_expected_move(symbol)
            if not prediction:
                continue
            
            base_em = prediction['em_baseline']
            earnings_date = prediction.get('earnings_date')
            
            # Generate forecasts for different horizons
            for days in horizon_days:
                exp_date = current_time.date() + timedelta(days=days)
                
                # Scale prediction based on time horizon
                time_scale = np.sqrt(days / 30)  # Scale by sqrt of time
                scaled_em = base_em * time_scale
                
                # Create forecast record
                forecast = {
                    'underlying': symbol,
                    'quote_ts': current_time,
                    'exp_date': exp_date,
                    'horizon': f'{days}d' if days != 30 else 'to_exp',
                    'em_baseline': scaled_em,
                    'band68_low': scaled_em * 0.7,
                    'band68_high': scaled_em * 1.3,
                    'band95_low': scaled_em * 0.5,
                    'band95_high': scaled_em * 1.5,
                    'model_type': prediction.get('model_type', 'linear'),
                    'confidence': prediction.get('confidence', 'medium')
                }
                
                forecasts.append(forecast)
            
            # Add earnings-specific forecast if available
            if earnings_date:
                try:
                    earnings_dt = pd.to_datetime(earnings_date).date()
                    if earnings_dt > current_time.date():
                        days_to_earnings = (earnings_dt - current_time.date()).days
                        if days_to_earnings <= 90:  # Only if within 3 months
                            earnings_scale = np.sqrt(days_to_earnings / 30)
                            earnings_em = base_em * earnings_scale
                            
                            earnings_forecast = {
                                'underlying': symbol,
                                'quote_ts': current_time,
                                'exp_date': earnings_dt,
                                'horizon': 'earnings',
                                'em_baseline': earnings_em,
                                'band68_low': earnings_em * 0.6,
                                'band68_high': earnings_em * 1.4,
                                'band95_low': earnings_em * 0.4,
                                'band95_high': earnings_em * 1.6,
                                'model_type': prediction.get('model_type', 'linear'),
                                'confidence': 'high'
                            }
                            
                            forecasts.append(earnings_forecast)
                except Exception:
                    pass  # Skip invalid earnings dates
                    
        except Exception as e:
            print(f"[ml] ⚠ Failed to generate forecast for {symbol}: {e}")
            continue
    
    print(f"[ml] Generated {len(forecasts)} total forecasts")
    return forecasts

def populate_forecasts(conn, forecasts):
    """Insert forecasts into DuckDB table."""
    print("[db] Inserting forecasts into DuckDB...")
    
    if not forecasts:
        print("[db] ⚠ No forecasts to insert")
        return
    
    # Convert to DataFrame for efficient insertion
    df = pd.DataFrame(forecasts)
    
    # Register DataFrame with DuckDB
    conn.register('forecasts_temp', df)
    
    # Insert into table
    conn.execute("""
        INSERT INTO em_forecasts 
        SELECT underlying, quote_ts, exp_date, horizon, em_baseline, 
               band68_low, band68_high, band95_low, band95_high, 
               model_type, confidence, CURRENT_TIMESTAMP as created_at
        FROM forecasts_temp
    """)
    
    # Unregister temporary table
    conn.unregister('forecasts_temp')
    
    # Verify insertion
    count = conn.execute("SELECT COUNT(*) FROM em_forecasts").fetchone()[0]
    print(f"[db] ✓ Inserted {count} forecast records")
    
    # Show sample data
    sample = conn.execute("""
        SELECT underlying, horizon, em_baseline, exp_date 
        FROM em_forecasts 
        ORDER BY underlying, exp_date 
        LIMIT 5
    """).fetchall()
    
    print("[db] Sample forecasts:")
    for row in sample:
        print(f"  {row[0]}: {row[1]} -> EM={row[2]:.3f}, exp={row[3]}")

def create_parquet_export(conn, data_dir):
    """Export forecasts to Parquet for backend integration."""
    print("[export] Creating Parquet export...")
    
    forecasts_dir = data_dir / "forecasts"
    forecasts_dir.mkdir(exist_ok=True)
    
    parquet_path = forecasts_dir / "em_forecasts.parquet"
    
    # Export to Parquet
    conn.execute(f"""
        COPY (SELECT * FROM em_forecasts) 
        TO '{parquet_path}' (FORMAT PARQUET)
    """)
    
    print(f"[export] ✓ Exported to {parquet_path}")
    
    # Create view for backend
    conn.execute(f"""
        CREATE OR REPLACE VIEW em_forecasts_view AS
        SELECT * FROM read_parquet('{parquet_path}')
    """)
    
    print("[export] ✓ Created em_forecasts_view")

def main():
    parser = argparse.ArgumentParser(description="Populate ML forecasts")
    parser.add_argument("--local", action="store_true", help="Use local data directory")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to process")
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_dir = project_root / "data"
    else:
        data_dir = Path("/srv/quantiv-data")
    
    duckdb_path = data_dir / "quantiv.duckdb"
    
    print("POPULATE ML FORECASTS")
    print("=" * 50)
    print(f"Data dir: {data_dir}")
    print(f"DuckDB: {duckdb_path}")
    
    if not duckdb_path.exists():
        print(f"⚠ DuckDB file not found: {duckdb_path}")
        print("Run the ML pipeline setup first:")
        print("  python scripts/build_em_comprehensive.py --local")
        sys.exit(1)
    
    # Initialize ML service
    try:
        ml_service = MLService(data_dir, duckdb_path)
        print(f"[ml] ✓ ML service initialized")
    except Exception as e:
        print(f"[ml] ✗ Failed to initialize ML service: {e}")
        sys.exit(1)
    
    # Get available symbols
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
        print(f"[ml] Using specified symbols: {symbols}")
    else:
        symbols = ml_service.get_available_symbols()
        print(f"[ml] Found {len(symbols)} symbols with ML data")
    
    if not symbols:
        print("[ml] ⚠ No symbols available for forecasting")
        sys.exit(1)
    
    # Connect to DuckDB
    conn = duckdb.connect(str(duckdb_path))
    
    try:
        # Create forecasts table
        create_forecasts_table(conn, data_dir)
        
        # Generate ML forecasts
        forecasts = generate_ml_forecasts(ml_service, symbols)
        
        # Populate database
        populate_forecasts(conn, forecasts)
        
        # Export to Parquet
        create_parquet_export(conn, data_dir)
        
        print("\n✅ ML forecasts populated successfully!")
        print("\nNext steps:")
        print("1. Start the backend: npm run dev:backend")
        print("2. Start the frontend: npm run dev:frontend") 
        print("3. Visit http://localhost:3000 to see ML predictions")
        
    finally:
        conn.close()
        ml_service.close()

if __name__ == "__main__":
    main()
