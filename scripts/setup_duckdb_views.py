#!/usr/bin/env python3
"""
Setup DuckDB views and configuration for Quantiv ML pipeline.

Creates normalized views for options chain and volatility history data,
with proper DuckDB configuration for performance.

Usage:
  python scripts/setup_duckdb_views.py [--data-root /srv/quantiv-data] [--duckdb-path /srv/quantiv-data/quantiv.duckdb]
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb

def setup_duckdb_config(conn):
    """Configure DuckDB for optimal performance."""
    print("[duckdb] Configuring DuckDB settings...")
    
    config_sql = """
    INSTALL httpfs; 
    LOAD httpfs;
    SET enable_object_cache = true;
    SET temp_directory = '/srv/quantiv-data/duckdb-cache';
    SET threads = 8;
    SET memory_limit = '4GB';
    SET max_memory = '8GB';
    """
    
    for statement in config_sql.strip().split(';'):
        if statement.strip():
            try:
                conn.execute(statement.strip())
                print(f"[duckdb] ✓ {statement.strip()}")
            except Exception as e:
                print(f"[duckdb] ⚠ {statement.strip()} -> {e}")

def create_raw_views(conn, data_root: Path):
    """Create raw data views that read directly from Parquet files."""
    print("[views] Creating raw data views...")
    
    options_path = data_root / "parquet" / "options_chain" / "*" / "*" / "*.parquet"
    volhist_path = data_root / "parquet" / "volatility_history" / "*" / "*" / "*.parquet"
    
    # Raw options view
    options_view_sql = f"""
    CREATE OR REPLACE VIEW v_options AS
    SELECT * FROM read_parquet('{options_path}');
    """
    
    # Raw volatility history view  
    volhist_view_sql = f"""
    CREATE OR REPLACE VIEW v_volhist AS
    SELECT * FROM read_parquet('{volhist_path}');
    """
    
    try:
        conn.execute(options_view_sql)
        print("[views] ✓ Created v_options")
    except Exception as e:
        print(f"[views] ⚠ v_options failed: {e}")
    
    try:
        conn.execute(volhist_view_sql)
        print("[views] ✓ Created v_volhist")
    except Exception as e:
        print(f"[views] ⚠ v_volhist failed: {e}")

def create_normalized_views(conn):
    """Create normalized views with consistent types and convenient date fields."""
    print("[views] Creating normalized views...")
    
    # Normalized options view
    options_norm_sql = """
    CREATE OR REPLACE VIEW v_options_norm AS
    SELECT
      date::TIMESTAMP           AS ts_utc,
      date::DATE                AS trade_date,
      act_symbol,
      expiration::DATE          AS expiration,
      strike::DOUBLE            AS strike,
      CASE 
        WHEN UPPER(TRIM(call_put)) IN ('C', 'CALL') THEN 'C'
        WHEN UPPER(TRIM(call_put)) IN ('P', 'PUT') THEN 'P'
        ELSE NULL
      END AS call_put,
      bid::DOUBLE               AS bid,
      ask::DOUBLE               AS ask, 
      vol::DOUBLE               AS vol,
      delta::DOUBLE             AS delta,
      gamma::DOUBLE             AS gamma,
      theta::DOUBLE             AS theta,
      vega::DOUBLE              AS vega,
      rho::DOUBLE               AS rho,
      now() AS created_at
    FROM v_options
    WHERE date IS NOT NULL 
      AND act_symbol IS NOT NULL
      AND expiration IS NOT NULL
      AND strike IS NOT NULL
      AND UPPER(TRIM(call_put)) IN ('C', 'CALL', 'P', 'PUT');
    """
    
    # Normalized volatility history view
    volhist_norm_sql = """
    CREATE OR REPLACE VIEW v_volhist_norm AS
    SELECT
      date::DATE::TIMESTAMP AS ts_utc,
      date::DATE AS trade_date,
      act_symbol,
      hv_current::DOUBLE        AS hv_current,
      hv_week_ago::DOUBLE       AS hv_week_ago,
      hv_month_ago::DOUBLE      AS hv_month_ago,
      hv_year_high::DOUBLE      AS hv_year_high,
      CASE WHEN hv_year_high_date IS NOT NULL 
           THEN hv_year_high_date::DATE 
           ELSE NULL END AS hv_year_high_date,
      hv_year_low::DOUBLE       AS hv_year_low,
      CASE WHEN hv_year_low_date IS NOT NULL 
           THEN hv_year_low_date::DATE 
           ELSE NULL END AS hv_year_low_date,
      iv_current::DOUBLE        AS iv_current,
      iv_week_ago::DOUBLE       AS iv_week_ago,
      iv_month_ago::DOUBLE      AS iv_month_ago,
      iv_year_high::DOUBLE      AS iv_year_high,
      CASE WHEN iv_year_high_date IS NOT NULL 
           THEN iv_year_high_date::DATE 
           ELSE NULL END AS iv_year_high_date,
      iv_year_low::DOUBLE       AS iv_year_low,
      CASE WHEN iv_year_low_date IS NOT NULL 
           THEN iv_year_low_date::DATE 
           ELSE NULL END AS iv_year_low_date
    FROM v_volhist
    WHERE date IS NOT NULL 
      AND act_symbol IS NOT NULL;
    """
    
    try:
        conn.execute(options_norm_sql)
        print("[views] ✓ Created v_options_norm")
    except Exception as e:
        print(f"[views] ⚠ v_options_norm failed: {e}")
    
    try:
        conn.execute(volhist_norm_sql)
        print("[views] ✓ Created v_volhist_norm")
    except Exception as e:
        print(f"[views] ⚠ v_volhist_norm failed: {e}")

def create_validation_queries(conn):
    """Create validation views for data quality checks."""
    print("[views] Creating validation views...")
    
    validation_sql = """
    -- Data quality summary view
    CREATE OR REPLACE VIEW v_data_quality AS
    SELECT
      'options' as dataset,
      COUNT(*) as total_rows,
      COUNT(DISTINCT act_symbol) as unique_symbols,
      MIN(trade_date) as min_date,
      MAX(trade_date) as max_date,
      COUNT(DISTINCT trade_date) as unique_dates
    FROM v_options_norm
    UNION ALL
    SELECT
      'volatility' as dataset,
      COUNT(*) as total_rows,
      COUNT(DISTINCT act_symbol) as unique_symbols,
      MIN(trade_date) as min_date,
      MAX(trade_date) as max_date,
      COUNT(DISTINCT trade_date) as unique_dates
    FROM v_volhist_norm;
    
    -- Year distribution view
    CREATE OR REPLACE VIEW v_year_distribution AS
    SELECT
      'options' as dataset,
      EXTRACT(YEAR FROM trade_date)::INT as year,
      COUNT(*) as row_count
    FROM v_options_norm
    GROUP BY 1, 2
    UNION ALL
    SELECT
      'volatility' as dataset,
      EXTRACT(YEAR FROM trade_date)::INT as year,
      COUNT(*) as row_count
    FROM v_volhist_norm
    GROUP BY 1, 2
    ORDER BY dataset, year;
    
    -- Join coverage view
    CREATE OR REPLACE VIEW v_join_coverage AS
    SELECT 
      EXTRACT(YEAR FROM o.trade_date)::INT as year,
      COUNT(*) as joined_rows,
      COUNT(DISTINCT o.act_symbol) as symbols_with_both
    FROM v_options_norm o
    JOIN v_volhist_norm v
      ON v.trade_date = o.trade_date 
      AND v.act_symbol = o.act_symbol
    GROUP BY 1 
    ORDER BY 1;
    """
    
    try:
        conn.execute(validation_sql)
        print("[views] ✓ Created validation views")
    except Exception as e:
        print(f"[views] ⚠ Validation views failed: {e}")

def run_healthcheck(conn):
    """Run basic healthcheck queries to validate the setup."""
    print("[healthcheck] Running data validation...")
    
    try:
        # Row counts
        result = conn.execute("SELECT * FROM v_data_quality").fetchall()
        print("[healthcheck] Data quality summary:")
        for row in result:
            dataset, total, symbols, min_date, max_date, dates = row
            print(f"  {dataset}: {total:,} rows, {symbols} symbols, {min_date} to {max_date} ({dates} dates)")
        
        # Year distribution
        result = conn.execute("SELECT * FROM v_year_distribution ORDER BY dataset, year").fetchall()
        print("[healthcheck] Year distribution:")
        current_dataset = None
        for row in result:
            dataset, year, count = row
            if dataset != current_dataset:
                print(f"  {dataset}:")
                current_dataset = dataset
            print(f"    {year}: {count:,} rows")
        
        # Join coverage
        result = conn.execute("SELECT * FROM v_join_coverage").fetchall()
        print("[healthcheck] Join coverage (options + volatility):")
        for row in result:
            year, joined_rows, symbols = row
            print(f"  {year}: {joined_rows:,} joined rows, {symbols} symbols")
            
    except Exception as e:
        print(f"[healthcheck] ⚠ Healthcheck failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="Setup DuckDB views for Quantiv")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("/srv/quantiv-data"),
        help="Root directory for data (default: /srv/quantiv-data)"
    )
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        help="Path to DuckDB file (default: {data-root}/quantiv.duckdb)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory instead of /srv/quantiv-data"
    )
    parser.add_argument(
        "--skip-healthcheck",
        action="store_true",
        help="Skip running healthcheck queries"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_root = project_root / "data"
    else:
        data_root = args.data_root
    
    if args.duckdb_path:
        duckdb_path = args.duckdb_path
    else:
        duckdb_path = data_root / "quantiv.duckdb"
    
    print(f"[setup] Data root: {data_root}")
    print(f"[setup] DuckDB path: {duckdb_path}")
    
    # Ensure data root exists
    data_root.mkdir(parents=True, exist_ok=True)
    
    try:
        # Connect to DuckDB
        conn = duckdb.connect(str(duckdb_path))
        print(f"[duckdb] Connected to: {duckdb_path}")
        
        # Setup configuration
        setup_duckdb_config(conn)
        
        # Create views
        create_raw_views(conn, data_root)
        create_normalized_views(conn)
        create_validation_queries(conn)
        
        # Run healthcheck unless skipped
        if not args.skip_healthcheck:
            run_healthcheck(conn)
        
        conn.close()
        print("[success] DuckDB views setup completed successfully")
        
    except Exception as e:
        print(f"[error] Failed to setup DuckDB views: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
