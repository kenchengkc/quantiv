#!/usr/bin/env python3
"""
Data validation healthcheck for Quantiv ML pipeline.

Runs comprehensive data quality checks on the DuckDB views to ensure
data integrity after each publish or update.

Usage:
  python scripts/data_healthcheck.py [--duckdb-path /srv/quantiv-data/quantiv.duckdb]
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
from datetime import datetime, timedelta

def run_basic_counts(conn):
    """Run basic row count checks."""
    print("=" * 60)
    print("BASIC ROW COUNTS")
    print("=" * 60)
    
    queries = [
        ("Options data", "SELECT COUNT(*) FROM v_options_norm"),
        ("Volatility data", "SELECT COUNT(*) FROM v_volhist_norm"),
        ("Raw options", "SELECT COUNT(*) FROM v_options"),
        ("Raw volatility", "SELECT COUNT(*) FROM v_volhist"),
    ]
    
    for name, query in queries:
        try:
            result = conn.execute(query).fetchone()
            count = result[0] if result else 0
            print(f"{name:20}: {count:,}")
        except Exception as e:
            print(f"{name:20}: ERROR - {e}")

def run_data_quality_checks(conn):
    """Run comprehensive data quality checks."""
    print("\n" + "=" * 60)
    print("DATA QUALITY SUMMARY")
    print("=" * 60)
    
    try:
        result = conn.execute("SELECT * FROM v_data_quality").fetchall()
        for row in result:
            dataset, total, symbols, min_date, max_date, dates = row
            print(f"\n{dataset.upper()} DATASET:")
            print(f"  Total rows:     {total:,}")
            print(f"  Unique symbols: {symbols:,}")
            print(f"  Date range:     {min_date} to {max_date}")
            print(f"  Trading days:   {dates:,}")
    except Exception as e:
        print(f"ERROR: {e}")

def run_year_distribution(conn):
    """Check year distribution for sanity."""
    print("\n" + "=" * 60)
    print("YEAR DISTRIBUTION")
    print("=" * 60)
    
    try:
        result = conn.execute("SELECT * FROM v_year_distribution ORDER BY dataset, year").fetchall()
        current_dataset = None
        for row in result:
            dataset, year, count = row
            if dataset != current_dataset:
                print(f"\n{dataset.upper()}:")
                current_dataset = dataset
            print(f"  {year}: {count:,} rows")
    except Exception as e:
        print(f"ERROR: {e}")

def run_join_coverage(conn):
    """Check join coverage between options and volatility data."""
    print("\n" + "=" * 60)
    print("JOIN COVERAGE (Options + Volatility)")
    print("=" * 60)
    
    try:
        result = conn.execute("SELECT * FROM v_join_coverage").fetchall()
        total_joined = 0
        for row in result:
            year, joined_rows, symbols = row
            total_joined += joined_rows
            print(f"  {year}: {joined_rows:,} joined rows, {symbols:,} symbols")
        print(f"\nTotal joined rows: {total_joined:,}")
    except Exception as e:
        print(f"ERROR: {e}")

def run_data_freshness_check(conn):
    """Check data freshness and recent activity."""
    print("\n" + "=" * 60)
    print("DATA FRESHNESS")
    print("=" * 60)
    
    # Check most recent data dates
    queries = [
        ("Options latest date", "SELECT MAX(trade_date) FROM v_options_norm"),
        ("Volatility latest date", "SELECT MAX(CAST(trade_date AS DATE)) FROM v_volhist_norm"),
    ]
    
    for name, query in queries:
        try:
            result = conn.execute(query).fetchone()
            latest_date = result[0] if result else None
            if latest_date:
                days_ago = (datetime.now().date() - latest_date).days
                print(f"{name:25}: {latest_date} ({days_ago} days ago)")
            else:
                print(f"{name:25}: No data")
        except Exception as e:
            print(f"{name:25}: ERROR - {e}")
    
    # Check recent activity (last 30 days)
    print("\nRecent activity (last 30 days):")
    recent_queries = [
        ("Options rows", """
            SELECT COUNT(*) FROM v_options_norm 
            WHERE trade_date >= current_date - INTERVAL '30' DAY
        """),
        ("Volatility rows", """
            SELECT COUNT(*) FROM v_volhist_norm 
            WHERE CAST(trade_date AS DATE) >= current_date - INTERVAL '30' DAY
        """),
        ("Active symbols", """
            SELECT COUNT(DISTINCT act_symbol) FROM v_options_norm 
            WHERE trade_date >= current_date - INTERVAL '30' DAY
        """),
    ]
    
    for name, query in recent_queries:
        try:
            result = conn.execute(query).fetchone()
            count = result[0] if result else 0
            print(f"  {name:20}: {count:,}")
        except Exception as e:
            print(f"  {name:20}: ERROR - {e}")

def run_symbol_analysis(conn):
    """Analyze symbol coverage and activity."""
    print("\n" + "=" * 60)
    print("SYMBOL ANALYSIS")
    print("=" * 60)
    
    # Top symbols by volume
    print("Top 10 symbols by row count (last 30 days):")
    try:
        result = conn.execute("""
            SELECT act_symbol, COUNT(*) as row_count
            FROM v_options_norm 
            WHERE trade_date >= current_date - INTERVAL '30' DAY
            GROUP BY act_symbol
            ORDER BY row_count DESC
            LIMIT 10
        """).fetchall()
        
        for symbol, count in result:
            print(f"  {symbol:10}: {count:,} rows")
    except Exception as e:
        print(f"ERROR: {e}")
    
    # Symbols with both options and volatility data
    print("\nSymbols with both options and volatility data (last 7 days):")
    try:
        result = conn.execute("""
            SELECT COUNT(DISTINCT o.act_symbol) as symbols_with_both
            FROM v_options_norm o
            JOIN v_volhist_norm v ON o.act_symbol = v.act_symbol AND o.trade_date = CAST(v.trade_date AS DATE)
            WHERE o.trade_date >= current_date - INTERVAL '7' DAY
        """).fetchone()
        
        count = result[0] if result else 0
        print(f"  Symbols with both datasets: {count:,}")
    except Exception as e:
        print(f"ERROR: {e}")

def run_data_integrity_checks(conn):
    """Run data integrity and consistency checks."""
    print("\n" + "=" * 60)
    print("DATA INTEGRITY CHECKS")
    print("=" * 60)
    
    integrity_checks = [
        ("Null act_symbol in options", "SELECT COUNT(*) FROM v_options WHERE act_symbol IS NULL"),
        ("Null dates in options", "SELECT COUNT(*) FROM v_options WHERE date IS NULL"),
        ("Invalid call_put values", "SELECT COUNT(*) FROM v_options WHERE call_put NOT IN ('C', 'P')"),
        ("Negative strikes", "SELECT COUNT(*) FROM v_options_norm WHERE strike <= 0"),
        ("Invalid bid/ask spreads", "SELECT COUNT(*) FROM v_options_norm WHERE bid > ask AND bid > 0 AND ask > 0"),
        ("Future expiration dates", "SELECT COUNT(*) FROM v_options_norm WHERE expiration < trade_date"),
    ]
    
    for check_name, query in integrity_checks:
        try:
            result = conn.execute(query).fetchone()
            count = result[0] if result else 0
            status = "✓ PASS" if count == 0 else f"⚠ FAIL ({count:,} issues)"
            print(f"  {check_name:30}: {status}")
        except Exception as e:
            print(f"  {check_name:30}: ERROR - {e}")

def run_performance_stats(conn):
    """Show performance and storage statistics."""
    print("\n" + "=" * 60)
    print("PERFORMANCE STATS")
    print("=" * 60)
    
    try:
        # Query performance test
        start_time = datetime.now()
        conn.execute("SELECT COUNT(*) FROM v_options_norm WHERE trade_date >= '2024-01-01'").fetchone()
        query_time = (datetime.now() - start_time).total_seconds()
        print(f"Sample query time: {query_time:.3f} seconds")
        
        # Memory usage
        result = conn.execute("SELECT current_setting('memory_limit')").fetchone()
        if result:
            print(f"Memory limit: {result[0]}")
            
    except Exception as e:
        print(f"Performance stats error: {e}")

def main():
    parser = argparse.ArgumentParser(description="Run Quantiv data healthcheck")
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=Path("/srv/quantiv-data/quantiv.duckdb"),
        help="Path to DuckDB file"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only basic checks (faster)"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        duckdb_path = project_root / "data" / "quantiv.duckdb"
    else:
        duckdb_path = args.duckdb_path
    
    if not duckdb_path.exists():
        print(f"[error] DuckDB file not found: {duckdb_path}")
        print("Run setup_duckdb_views.py first to create the database and views.")
        sys.exit(1)
    
    print(f"QUANTIV DATA HEALTHCHECK")
    print(f"Database: {duckdb_path}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    try:
        conn = duckdb.connect(str(duckdb_path))
        
        # Always run basic checks
        run_basic_counts(conn)
        run_data_quality_checks(conn)
        
        if not args.quick:
            run_year_distribution(conn)
            run_join_coverage(conn)
            run_data_freshness_check(conn)
            run_symbol_analysis(conn)
            run_data_integrity_checks(conn)
            run_performance_stats(conn)
        
        conn.close()
        
        print("\n" + "=" * 60)
        print("HEALTHCHECK COMPLETE")
        print("=" * 60)
        print("Review any ERROR or FAIL items above.")
        print("For issues, check data pipeline and re-run setup scripts.")
        
    except Exception as e:
        print(f"[error] Healthcheck failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
