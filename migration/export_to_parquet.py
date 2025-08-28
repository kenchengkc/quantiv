#!/usr/bin/env python3
"""
Quantiv PostgreSQL to Parquet Migration Script

This script exports data from PostgreSQL partitions to Parquet files
with proper Hive-style partitioning for optimal DuckDB performance.
"""

import asyncio
import asyncpg
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import os
from pathlib import Path
from datetime import datetime, date
import logging
from typing import List, Dict, Any
import argparse
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class QuantivParquetExporter:
    def __init__(self, pg_host: str = "localhost", pg_port: int = 5432,
                 pg_user: str = "quantiv_user", pg_password: str = "quantiv_secure_2024",
                 pg_database: str = "quantiv_options", output_dir: str = "./data"):
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.pg_user = pg_user
        self.pg_password = pg_password
        self.pg_database = pg_database
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    async def get_connection(self) -> asyncpg.Connection:
        """Create PostgreSQL connection."""
        return await asyncpg.connect(
            host=self.pg_host,
            port=self.pg_port,
            user=self.pg_user,
            password=self.pg_password,
            database=self.pg_database
        )
    
    async def export_options_chain(self, batch_size: int = 100000) -> None:
        """Export options_chain table with year/month partitioning."""
        logger.info("Starting options_chain export...")
        
        conn = await self.get_connection()
        try:
            # Get partition information
            partitions = await conn.fetch("""
                SELECT schemaname, tablename 
                FROM pg_tables 
                WHERE tablename LIKE 'options_chain_%' 
                  AND schemaname = 'public'
                ORDER BY tablename
            """)
            
            for partition in tqdm(partitions, desc="Exporting options partitions"):
                table_name = partition['tablename']
                year = table_name.split('_')[-1]
                
                logger.info(f"Exporting partition: {table_name}")
                
                # Get total count for progress bar
                count_query = f"SELECT COUNT(*) FROM {table_name}"
                total_rows = await conn.fetchval(count_query)
                
                if total_rows == 0:
                    logger.info(f"Skipping empty partition: {table_name}")
                    continue
                
                # Process in batches by month
                months_query = f"""
                    SELECT DISTINCT EXTRACT(MONTH FROM date) as month
                    FROM {table_name}
                    ORDER BY month
                """
                months = await conn.fetch(months_query)
                
                for month_row in months:
                    month = int(month_row['month'])
                    await self._export_options_month(conn, table_name, year, month, batch_size)
                    
        finally:
            await conn.close()
            
        logger.info("Options chain export completed!")
    
    async def _export_options_month(self, conn: asyncpg.Connection, table_name: str, 
                                   year: str, month: int, batch_size: int) -> None:
        """Export a single month of options data."""
        month_str = f"{month:02d}"
        output_path = self.output_dir / "options" / f"year={year}" / f"month={month_str}"
        output_path.mkdir(parents=True, exist_ok=True)
        
        parquet_file = output_path / f"options_{year}_{month_str}.parquet"
        
        if parquet_file.exists():
            logger.info(f"Skipping existing file: {parquet_file}")
            return
            
        query = f"""
            SELECT 
                id, date, act_symbol, expiration, strike, call_put,
                bid, ask, vol, delta, gamma, theta, vega,
                open_interest, volume, created_at
            FROM {table_name}
            WHERE EXTRACT(MONTH FROM date) = $1
            ORDER BY date, act_symbol, strike
        """
        
        # Process in chunks to handle large datasets
        offset = 0
        all_data = []
        
        while True:
            chunk_query = f"{query} LIMIT {batch_size} OFFSET {offset}"
            rows = await conn.fetch(chunk_query, month)
            
            if not rows:
                break
                
            # Convert to pandas DataFrame
            df = pd.DataFrame([dict(r) for r in rows])
            all_data.append(df)
            offset += batch_size
            
        if all_data:
            # Combine all chunks
            final_df = pd.concat(all_data, ignore_index=True)
            
            # Optimize data types for Parquet
            final_df = self._optimize_dtypes(final_df)
            
            # Write to Parquet with compression
            final_df.to_parquet(
                parquet_file,
                engine='pyarrow',
                compression='snappy',
                index=False
            )
            
            logger.info(f"Exported {len(final_df)} rows to {parquet_file}")
    
    async def export_volatility_history(self) -> None:
        """Export volatility_history table by year."""
        logger.info("Starting volatility_history export...")
        
        conn = await self.get_connection()
        try:
            # Get year range
            years_query = """
                SELECT DISTINCT EXTRACT(YEAR FROM date) as year
                FROM volatility_history
                ORDER BY year
            """
            years = await conn.fetch(years_query)
            
            for year_row in tqdm(years, desc="Exporting volatility years"):
                year = int(year_row['year'])
                await self._export_volatility_year(conn, year)
                
        finally:
            await conn.close()
            
        logger.info("Volatility history export completed!")
    
    async def _export_volatility_year(self, conn: asyncpg.Connection, year: int) -> None:
        """Export a single year of volatility data."""
        output_path = self.output_dir / "volatility" / f"year={year}"
        output_path.mkdir(parents=True, exist_ok=True)
        
        parquet_file = output_path / f"volatility_{year}.parquet"
        
        if parquet_file.exists():
            logger.info(f"Skipping existing file: {parquet_file}")
            return
            
        query = """
            SELECT id, date, symbol, iv, hv, iv_rank, iv_percentile, created_at
            FROM volatility_history
            WHERE EXTRACT(YEAR FROM date) = $1
            ORDER BY date, symbol
        """
        
        rows = await conn.fetch(query, year)
        
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            df = self._optimize_dtypes(df)
            
            df.to_parquet(
                parquet_file,
                engine='pyarrow',
                compression='snappy',
                index=False
            )
            
            logger.info(f"Exported {len(df)} volatility rows to {parquet_file}")
    
    async def export_ml_tables(self) -> None:
        """Export ML pipeline tables."""
        logger.info("Starting ML tables export...")
        
        tables = [
            ('em_forecasts', 'forecasts'),
            ('atm_features', 'forecasts'),
            ('em_labels', 'forecasts'),
            ('symbols_metadata', 'metadata'),
            ('model_meta', 'metadata'),
            ('model_performance', 'metadata')
        ]
        
        conn = await self.get_connection()
        try:
            for table_name, subfolder in tqdm(tables, desc="Exporting ML tables"):
                await self._export_table(conn, table_name, subfolder)
        finally:
            await conn.close()
            
        logger.info("ML tables export completed!")
    
    async def _export_table(self, conn: asyncpg.Connection, table_name: str, subfolder: str) -> None:
        """Export a complete table to Parquet."""
        output_path = self.output_dir / subfolder
        output_path.mkdir(parents=True, exist_ok=True)
        
        parquet_file = output_path / f"{table_name}.parquet"
        
        if parquet_file.exists():
            logger.info(f"Skipping existing file: {parquet_file}")
            return
            
        try:
            # Check if table exists
            exists_query = """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = $1
                )
            """
            exists = await conn.fetchval(exists_query, table_name)
            
            if not exists:
                logger.warning(f"Table {table_name} does not exist, skipping...")
                return
            
            query = f"SELECT * FROM {table_name} ORDER BY 1"
            rows = await conn.fetch(query)
            
            if rows:
                df = pd.DataFrame([dict(r) for r in rows])
                df = self._optimize_dtypes(df)
                
                df.to_parquet(
                    parquet_file,
                    engine='pyarrow',
                    compression='snappy',
                    index=False
                )
                
                logger.info(f"Exported {len(df)} rows from {table_name}")
            else:
                logger.info(f"No data in table {table_name}")
                
        except Exception as e:
            logger.error(f"Error exporting {table_name}: {e}")
    
    def _optimize_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimize DataFrame data types for Parquet storage."""
        # Convert decimal columns to appropriate float types
        decimal_cols = ['strike', 'bid', 'ask', 'vol', 'delta', 'gamma', 'theta', 'vega']
        for col in decimal_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype('float32')
        
        # Convert integer columns
        int_cols = ['open_interest', 'volume', 'id']
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int32')
        
        # Ensure string columns are properly typed
        str_cols = ['act_symbol', 'call_put', 'symbol', 'underlying', 'horizon']
        for col in str_cols:
            if col in df.columns:
                df[col] = df[col].astype('string')
        
        # Convert date columns
        date_cols = ['date', 'expiration', 'exp_date', 'quote_date']
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col]).dt.date
        
        # Convert timestamp columns
        ts_cols = ['created_at', 'quote_ts', 'trained_at']
        for col in ts_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        
        return df
    
    async def validate_export(self) -> Dict[str, Any]:
        """Validate exported data against original PostgreSQL."""
        logger.info("Validating exported data...")
        
        validation_results = {}
        
        conn = await self.get_connection()
        try:
            # Count original rows
            tables_to_check = [
                'options_chain',
                'volatility_history',
                'em_forecasts',
                'symbols_metadata'
            ]
            
            for table in tables_to_check:
                try:
                    original_count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                    validation_results[table] = {
                        'original_count': original_count,
                        'exported_files': self._count_parquet_rows(table)
                    }
                except Exception as e:
                    logger.warning(f"Could not validate {table}: {e}")
                    
        finally:
            await conn.close()
        
        return validation_results
    
    def _count_parquet_rows(self, table_name: str) -> int:
        """Count rows in exported Parquet files."""
        total_rows = 0
        
        if table_name == 'options_chain':
            options_path = self.output_dir / "options"
            if options_path.exists():
                for parquet_file in options_path.rglob("*.parquet"):
                    df = pd.read_parquet(parquet_file)
                    total_rows += len(df)
        else:
            # Check other tables in their respective folders
            for subfolder in ['volatility', 'forecasts', 'metadata']:
                folder_path = self.output_dir / subfolder
                if folder_path.exists():
                    for parquet_file in folder_path.glob(f"{table_name}*.parquet"):
                        df = pd.read_parquet(parquet_file)
                        total_rows += len(df)
        
        return total_rows


async def main():
    parser = argparse.ArgumentParser(description='Export Quantiv PostgreSQL data to Parquet')
    parser.add_argument('--host', default='localhost', help='PostgreSQL host')
    parser.add_argument('--port', default=5432, type=int, help='PostgreSQL port')
    parser.add_argument('--user', default='quantiv_user', help='PostgreSQL user')
    parser.add_argument('--password', default='quantiv_secure_2024', help='PostgreSQL password')
    parser.add_argument('--database', default='quantiv_options', help='PostgreSQL database')
    parser.add_argument('--output-dir', default='./data', help='Output directory for Parquet files')
    parser.add_argument('--batch-size', default=100000, type=int, help='Batch size for processing')
    parser.add_argument('--validate', action='store_true', help='Validate exported data')
    
    args = parser.parse_args()
    
    exporter = QuantivParquetExporter(
        pg_host=args.host,
        pg_port=args.port,
        pg_user=args.user,
        pg_password=args.password,
        pg_database=args.database,
        output_dir=args.output_dir
    )
    
    try:
        # Export all tables
        await exporter.export_options_chain(batch_size=args.batch_size)
        await exporter.export_volatility_history()
        await exporter.export_ml_tables()
        
        if args.validate:
            validation_results = await exporter.validate_export()
            logger.info("Validation Results:")
            for table, results in validation_results.items():
                logger.info(f"  {table}: {results['original_count']} original → {results['exported_files']} exported")
        
        logger.info("Migration export completed successfully!")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
