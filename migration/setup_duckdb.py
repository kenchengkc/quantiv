#!/usr/bin/env python3
"""
DuckDB Setup Script for Quantiv Migration

This script creates the DuckDB database and sets up views/tables
that point to the exported Parquet files.
"""

import duckdb
import os
from pathlib import Path
import logging
import argparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DuckDBSetup:
    def __init__(self, data_dir: str = "./data", db_file: str = "./quantiv.duckdb"):
        self.data_dir = Path(data_dir).resolve()
        self.db_file = db_file
        self.conn = None
        
    def connect(self):
        """Create DuckDB connection."""
        self.conn = duckdb.connect(self.db_file)
        logger.info(f"Connected to DuckDB: {self.db_file}")
        
    def setup_extensions(self):
        """Install and load necessary DuckDB extensions."""
        logger.info("Setting up DuckDB extensions...")
        
        extensions = ['parquet', 'httpfs']
        for ext in extensions:
            try:
                self.conn.execute(f"INSTALL {ext}")
                self.conn.execute(f"LOAD {ext}")
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.warning(f"Could not load extension {ext}: {e}")
    
    def setup_options_chain_view(self):
        """Create options_chain view from partitioned Parquet files."""
        logger.info("Creating options_chain view...")
        
        options_path = self.data_dir / "options"
        if not options_path.exists():
            logger.warning("Options data directory not found, skipping options_chain view")
            return
            
        # Create view that reads from all partitioned Parquet files
        view_sql = f"""
        CREATE OR REPLACE VIEW options_chain AS
        SELECT 
            CAST("0" AS BIGINT) AS id,
            CAST("1" AS DATE) AS date,
            CAST("2" AS VARCHAR) AS act_symbol,
            CAST("3" AS DATE) AS expiration,
            CAST("4" AS DOUBLE) AS strike,
            CAST("5" AS VARCHAR) AS call_put,
            CAST("6" AS DOUBLE) AS bid,
            CAST("7" AS DOUBLE) AS ask,
            CAST("8" AS DOUBLE) AS vol,
            CAST("9" AS DOUBLE) AS delta,
            CAST("10" AS DOUBLE) AS gamma,
            CAST("11" AS DOUBLE) AS theta,
            CAST("12" AS DOUBLE) AS vega,
            CAST("13" AS BIGINT) AS open_interest,
            CAST("14" AS BIGINT) AS volume,
            CAST("15" AS TIMESTAMP) AS created_at,
            CAST(year AS INTEGER) as partition_year,
            CAST(month AS INTEGER) as partition_month
        FROM read_parquet('{options_path}/**/options_*.parquet', hive_partitioning=true, union_by_name=true)
        """
        
        self.conn.execute(view_sql)
        
        # Test the view
        count = self.conn.execute("SELECT COUNT(*) FROM options_chain").fetchone()[0]
        logger.info(f"Options chain view created with {count:,} rows")
    
    def setup_volatility_view(self):
        """Create volatility_history view from Parquet files."""
        logger.info("Creating volatility_history view...")
        
        volatility_path = self.data_dir / "volatility"
        if not volatility_path.exists():
            logger.warning("Volatility data directory not found, skipping volatility_history view")
            return
            
        view_sql = f"""
        CREATE OR REPLACE VIEW volatility_history AS
        SELECT 
            CAST("0" AS BIGINT) AS id,
            CAST("1" AS DATE) AS date,
            CAST("2" AS VARCHAR) AS symbol,
            CAST("3" AS DOUBLE) AS iv,
            CAST("4" AS DOUBLE) AS hv,
            CAST("5" AS DOUBLE) AS iv_rank,
            CAST("6" AS DOUBLE) AS iv_percentile,
            CAST("7" AS TIMESTAMP) AS created_at,
            CAST(year AS INTEGER) as partition_year
        FROM read_parquet('{volatility_path}/**/volatility_*.parquet', hive_partitioning=true, union_by_name=true)
        """
        
        self.conn.execute(view_sql)
        
        # Test the view
        count = self.conn.execute("SELECT COUNT(*) FROM volatility_history").fetchone()[0]
        logger.info(f"Volatility history view created with {count:,} rows")
    
    def setup_ml_tables(self):
        """Create ML tables from Parquet files."""
        logger.info("Setting up ML tables...")
        
        tables = [
            ('em_forecasts', 'forecasts'),
            ('atm_features', 'forecasts'),
            ('em_labels', 'forecasts'),
            ('symbols_metadata', 'metadata'),
            ('model_meta', 'metadata'),
            ('model_performance', 'metadata')
        ]
        
        for table_name, subfolder in tables:
            parquet_path = self.data_dir / subfolder / f"{table_name}.parquet"
            
            if parquet_path.exists():
                view_sql = f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT * FROM read_parquet('{parquet_path}')
                """
                
                self.conn.execute(view_sql)
                
                # Test the table
                try:
                    count = self.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                    logger.info(f"Created {table_name} view with {count:,} rows")
                except Exception as e:
                    logger.warning(f"Could not count rows in {table_name}: {e}")
            else:
                logger.warning(f"Parquet file not found for {table_name}: {parquet_path}")
    
    def create_analytical_views(self):
        """Recreate analytical views from original PostgreSQL schema."""
        logger.info("Creating analytical views...")
        
        # Daily IV Summary view
        daily_iv_sql = """
        CREATE OR REPLACE VIEW daily_iv_summary AS
        SELECT 
            date,
            act_symbol,
            AVG(vol) as avg_iv,
            MEDIAN(vol) as median_iv,
            COUNT(*) as option_count
        FROM options_chain 
        WHERE vol IS NOT NULL
        GROUP BY date, act_symbol
        """
        
        self.conn.execute(daily_iv_sql)
        logger.info("Created daily_iv_summary view")
        
        # ATM Options view (simplified for DuckDB)
        atm_options_sql = """
        CREATE OR REPLACE VIEW atm_options AS
        WITH symbol_avg_strikes AS (
            SELECT 
                act_symbol, 
                date,
                AVG(strike) as avg_strike
            FROM options_chain 
            GROUP BY act_symbol, date
        )
        SELECT o.*
        FROM options_chain o
        JOIN symbol_avg_strikes s 
            ON o.act_symbol = s.act_symbol 
            AND o.date = s.date
        WHERE ABS(o.strike - s.avg_strike) < 5.0
        """
        
        self.conn.execute(atm_options_sql)
        logger.info("Created atm_options view")
        
        # Options summary by symbol
        symbol_summary_sql = """
        CREATE OR REPLACE VIEW symbol_summary AS
        SELECT 
            act_symbol,
            MIN(date) as first_date,
            MAX(date) as last_date,
            COUNT(*) as total_options,
            AVG(vol) as avg_iv,
            COUNT(DISTINCT date) as trading_days
        FROM options_chain
        WHERE vol IS NOT NULL
        GROUP BY act_symbol
        """
        
        self.conn.execute(symbol_summary_sql)
        logger.info("Created symbol_summary view")
    
    def create_performance_optimizations(self):
        """Create materialized tables for frequently accessed data."""
        logger.info("Creating performance optimizations...")
        
        # Create a recent options table (materialized view equivalent)
        recent_options_sql = """
        CREATE OR REPLACE TABLE recent_options AS
        SELECT * 
        FROM options_chain 
        WHERE date >= (SELECT MAX(date) FROM options_chain) - INTERVAL 30 DAY
        """
        
        self.conn.execute(recent_options_sql)
        logger.info("Created recent_options materialized table")
        
        # Create symbol metadata table from summary
        symbol_metadata_sql = """
        CREATE OR REPLACE TABLE symbols_metadata_computed AS
        SELECT 
            act_symbol as symbol,
            first_date,
            last_date,
            total_options,
            avg_iv,
            CURRENT_TIMESTAMP as updated_at
        FROM symbol_summary
        """
        
        self.conn.execute(symbol_metadata_sql)
        logger.info("Created symbols_metadata_computed table")
    
    def test_queries(self):
        """Test key queries to ensure performance."""
        logger.info("Testing key queries...")
        
        test_queries = [
            ("Symbol count", "SELECT COUNT(DISTINCT act_symbol) FROM options_chain"),
            ("Date range", "SELECT MIN(date), MAX(date) FROM options_chain"),
            ("Recent activity", "SELECT COUNT(*) FROM recent_options"),
            ("Average IV by symbol", """
                SELECT act_symbol, AVG(vol) as avg_iv 
                FROM options_chain 
                WHERE vol IS NOT NULL 
                GROUP BY act_symbol 
                ORDER BY avg_iv DESC 
                LIMIT 5
            """)
        ]
        
        for test_name, query in test_queries:
            try:
                result = self.conn.execute(query).fetchall()
                logger.info(f"✓ {test_name}: {result}")
            except Exception as e:
                logger.error(f"✗ {test_name} failed: {e}")
    
    def setup_database_settings(self):
        """Configure DuckDB for optimal performance."""
        logger.info("Configuring DuckDB settings...")
        
        # Optimize for analytical workloads
        settings = [
            ("memory_limit", "2GB"),
            ("threads", "4"),
            ("temp_directory", "/tmp/duckdb_temp")
        ]
        
        for setting, value in settings:
            try:
                self.conn.execute(f"SET {setting} = '{value}'")
                logger.info(f"Set {setting} = {value}")
            except Exception as e:
                logger.warning(f"Could not set {setting}: {e}")
    
    def export_schema_info(self):
        """Export schema information for documentation."""
        logger.info("Exporting schema information...")
        
        # Get table/view information
        tables_query = """
        SELECT table_name, table_type 
        FROM information_schema.tables 
        WHERE table_schema = 'main'
        ORDER BY table_name
        """
        
        tables = self.conn.execute(tables_query).fetchall()
        
        schema_info = []
        for table_name, table_type in tables:
            # Get column info
            columns_query = f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = '{table_name}' AND table_schema = 'main'
            ORDER BY ordinal_position
            """
            columns = self.conn.execute(columns_query).fetchall()
            
            schema_info.append({
                'name': table_name,
                'type': table_type,
                'columns': columns
            })
        
        # Write schema to file
        schema_file = Path(self.db_file).parent / "duckdb_schema.txt"
        with open(schema_file, 'w') as f:
            f.write("DuckDB Schema Information\n")
            f.write("=" * 50 + "\n\n")
            
            for table_info in schema_info:
                f.write(f"{table_info['type']}: {table_info['name']}\n")
                f.write("-" * 30 + "\n")
                for col_name, col_type in table_info['columns']:
                    f.write(f"  {col_name}: {col_type}\n")
                f.write("\n")
        
        logger.info(f"Schema information exported to: {schema_file}")
    
    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")


def main():
    parser = argparse.ArgumentParser(description='Setup DuckDB for Quantiv migration')
    parser.add_argument('--data-dir', default='./data', help='Directory containing Parquet files')
    parser.add_argument('--db-file', default='./quantiv.duckdb', help='DuckDB database file')
    parser.add_argument('--skip-test', action='store_true', help='Skip test queries')
    
    args = parser.parse_args()
    
    setup = DuckDBSetup(data_dir=args.data_dir, db_file=args.db_file)
    
    try:
        setup.connect()
        setup.setup_extensions()
        setup.setup_database_settings()
        
        # Create views and tables
        setup.setup_options_chain_view()
        setup.setup_volatility_view()
        setup.setup_ml_tables()
        setup.create_analytical_views()
        setup.create_performance_optimizations()
        
        # Test and document
        if not args.skip_test:
            setup.test_queries()
        
        setup.export_schema_info()
        
        logger.info("DuckDB setup completed successfully!")
        
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        raise
    finally:
        setup.close()


if __name__ == "__main__":
    main()
