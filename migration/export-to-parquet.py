#!/usr/bin/env python3
"""
Export PostgreSQL options data to Parquet format for new ML pipeline
Uses DuckDB for efficient partitioning: underlying=SYMBOL/quote_year=YYYY/quote_month=MM/
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DuckDBParquetExporter:
    def __init__(self):
        self.pg_url = "postgresql://quantiv_user:quantiv_secure_2024@localhost:5432/quantiv_options"
        self.output_dir = Path("/Users/ken/Desktop/quantiv/data/parquet")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize DuckDB connection with memory optimization
        self.conn = duckdb.connect()
        
        # Optimize for limited memory (1GB system)
        self.conn.execute("SET memory_limit='2GB'")
        self.conn.execute("SET threads=2")
        self.conn.execute("SET preserve_insertion_order=false")
        
        # Install and load PostgreSQL extension
        self.conn.execute("INSTALL postgres")
        self.conn.execute("LOAD postgres")
        
        # Attach PostgreSQL database
        self.conn.execute(f"""
            ATTACH '{self.pg_url}' AS pg_db (TYPE postgres)
        """)
        
    def export_options_chains_duckdb(self):
        """Export options chains using DuckDB with memory-efficient chunking"""
        logger.info("🚀 Exporting options chains to Parquet with DuckDB...")
        
        # Get data summary
        summary = self.conn.execute("""
            SELECT 
                COUNT(*) as total_rows,
                MIN(date) as min_date,
                MAX(date) as max_date,
                COUNT(DISTINCT act_symbol) as symbol_count
            FROM pg_db.options_chain_2024
        """).fetchone()
        
        logger.info(f"📊 Total rows: {summary[0]:,}")
        logger.info(f"📅 Date range: {summary[1]} to {summary[2]}")
        logger.info(f"🏷️  Symbols: {summary[3]}")
        
        # Export by month to manage memory usage
        months = self.conn.execute("""
            SELECT DISTINCT 
                EXTRACT(YEAR FROM date) as year,
                EXTRACT(MONTH FROM date) as month
            FROM pg_db.options_chain_2024
            ORDER BY year, month
        """).fetchall()
        
        output_path = self.output_dir / "options_chains"
        logger.info(f"📂 Exporting to: {output_path}")
        
        for year, month in months:
            logger.info(f"📦 Processing {int(year)}-{int(month):02d}...")
            
            # Export one month at a time to manage memory
            self.conn.execute(f"""
                COPY (
                    SELECT 
                        act_symbol as underlying,
                        date::TIMESTAMP as quote_ts,
                        expiration::DATE as exp_date,
                        call_put as callput,
                        strike,
                        (bid + ask) / 2.0 as contract_price,
                        bid,
                        ask,
                        delta,
                        gamma, 
                        theta,
                        vega,
                        vol as iv,
                        NULL as spot,
                        volume,
                        open_interest,
                        (expiration::DATE - date::DATE) as dte,
                        {int(year)} as quote_year,
                        {int(month)} as quote_month
                    FROM pg_db.options_chain_2024
                    WHERE EXTRACT(YEAR FROM date) = {int(year)}
                      AND EXTRACT(MONTH FROM date) = {int(month)}
                      AND bid > 0 
                      AND ask > bid
                      AND vol > 0
                ) TO '{output_path}'
                (FORMAT PARQUET, 
                 PARTITION_BY (underlying, quote_year, quote_month),
                 COMPRESSION 'ZSTD',
                 ROW_GROUP_SIZE 50000,
                 OVERWRITE_OR_IGNORE true)
            """)
        
        logger.info("✅ Options chains exported with partitioning")
    
    def create_serving_schema(self):
        """Create optimized PostgreSQL serving schema"""
        logger.info("🗃️  Creating serving schema...")
        
        # Create serving tables
        serving_schema = """
        -- Expected Move Forecasts serving table (align with create-em-schema.sql)
        DROP TABLE IF EXISTS em_forecasts CASCADE;
        CREATE TABLE em_forecasts (
            underlying TEXT NOT NULL,
            quote_ts TIMESTAMPTZ NOT NULL,
            exp_date DATE NOT NULL,
            horizon TEXT NOT NULL,                  -- 'to_exp','1d','5d'
            
            -- Model outputs
            em_baseline DOUBLE PRECISION,           -- S*ATM_IV*sqrt(T/365)
            em_calibrated DOUBLE PRECISION,         -- α̂ * em_baseline
            em_quantile DOUBLE PRECISION,           -- direct quantile prediction
            
            -- Confidence bands
            band68_low DOUBLE PRECISION,
            band68_high DOUBLE PRECISION,
            band95_low DOUBLE PRECISION,
            band95_high DOUBLE PRECISION,
            
            -- Model metadata
            model_version TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (underlying, quote_ts, exp_date, horizon)
        );
        
        -- Optimized indexes for serving
        CREATE INDEX IF NOT EXISTS idx_em_forecasts_lookup ON em_forecasts (underlying, exp_date, horizon);
        CREATE INDEX IF NOT EXISTS idx_em_forecasts_recent ON em_forecasts (quote_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_em_forecasts_symbol_recent ON em_forecasts (underlying, quote_ts DESC);
        
        -- Optional model metadata table
        DROP TABLE IF EXISTS model_meta CASCADE;
        CREATE TABLE model_meta (
            model_name TEXT PRIMARY KEY,
            trained_at TIMESTAMPTZ,
            version TEXT,
            notes TEXT,
            performance_metrics JSONB
        );
        """
        
        self.conn.execute(f"""ATTACH '{self.pg_url}' AS serving_db (TYPE postgres)""")
        
        # Execute schema creation
        for statement in serving_schema.split(';'):
            if statement.strip():
                try:
                    self.conn.execute(f"""EXECUTE ('{statement.strip()}') AT serving_db""")
                except Exception as e:
                    logger.warning(f"Schema statement failed: {e}")
        
        logger.info("✅ Serving schema created")
    def verify_parquet_export(self):
        """Verify the exported Parquet files"""
        logger.info("🔍 Verifying Parquet export...")
        
        parquet_path = self.output_dir / "options_chains"
        
        if not parquet_path.exists():
            logger.error("❌ Parquet export directory not found")
            return False
        
        # Count partitions and files
        partitions = list(parquet_path.glob("underlying=*/quote_year=*/quote_month=*"))
        total_files = list(parquet_path.glob("**/*.parquet"))
        
        logger.info(f"📁 Partitions created: {len(partitions)}")
        logger.info(f"📄 Parquet files: {len(total_files)}")
        
        # Calculate total size
        total_size = sum(f.stat().st_size for f in total_files)
        size_gb = total_size / (1024**3)
        
        logger.info(f"💾 Total size: {size_gb:.2f} GB")
        
        # Sample verification - read a small partition
        if partitions:
            sample_partition = partitions[0]
            try:
                sample_df = self.conn.execute(f"""
                    SELECT COUNT(*), underlying, quote_year, quote_month
                    FROM read_parquet('{sample_partition}/*.parquet')
                    GROUP BY underlying, quote_year, quote_month
                    LIMIT 5
                """).fetchdf()
                
                logger.info(f"📊 Sample partition data:")
                logger.info(sample_df.to_string())
                
            except Exception as e:
                logger.warning(f"Sample read failed: {e}")
        
        return True
    def close(self):
        """Close DuckDB connection"""
        self.conn.close()
    
    def _clean_options_data(self, df):
        """Clean and optimize data types"""
        # Convert dates
        df['date'] = pd.to_datetime(df['date'])
        df['expiration'] = pd.to_datetime(df['expiration'])
        
        # Optimize numeric types
        df['strike'] = df['strike'].astype('float32')
        df['bid'] = df['bid'].astype('float32')
        df['ask'] = df['ask'].astype('float32')
        df['mid_price'] = df['mid_price'].astype('float32')
        df['underlying_price'] = df['underlying_price'].astype('float32')
        df['implied_volatility'] = df['implied_volatility'].astype('float32')
        
        # Greeks as float32
        for col in ['delta', 'gamma', 'theta', 'vega', 'rho']:
            if col in df.columns:
                df[col] = df[col].astype('float32')
        
        # Integer types
        if 'volume' in df.columns:
            df['volume'] = df['volume'].fillna(0).astype('int32')
        if 'open_interest' in df.columns:
            df['open_interest'] = df['open_interest'].fillna(0).astype('int32')
        df['dte'] = df['dte'].astype('int16')
        
        # Categories for strings
        df['symbol'] = df['symbol'].astype('category')
        df['call_put'] = df['call_put'].astype('category')
        
        return df
    
    def export_volatility_history(self):
        """Export volatility history table"""
        logger.info("📈 Exporting volatility history...")
        
        query = """
        SELECT 
            date,
            symbol,
            implied_volatility as iv,
            historical_volatility as hv,
            iv_rank,
            iv_percentile
        FROM volatility_history
        ORDER BY date, symbol
        """
        
        try:
            df = pd.read_sql(query, self.engine)
            
            if len(df) > 0:
                # Clean data
                df['date'] = pd.to_datetime(df['date'])
                df['symbol'] = df['symbol'].astype('category')
                
                for col in ['iv', 'hv', 'iv_rank', 'iv_percentile']:
                    if col in df.columns:
                        df[col] = df[col].astype('float32')
                
                # Save to Parquet
                output_file = self.output_dir / "volatility_history.parquet"
                df.to_parquet(output_file, engine='pyarrow', compression='snappy', index=False)
                
                logger.info(f"✅ Volatility history: {len(df):,} rows exported")
            else:
                logger.info("⚠️  No volatility history data found")
                
        except Exception as e:
            logger.warning(f"⚠️  Volatility history export failed: {e}")
    
    def create_metadata_file(self):
        """Create metadata file with export information"""
        metadata = {
            "export_timestamp": datetime.now().isoformat(),
            "source": "PostgreSQL quantiv_options database",
            "format": "Parquet with ZSTD compression",
            "partitioning": "underlying/quote_year/quote_month for options_chains",
            "schema_version": "1.0",
            "notes": "Exported via DuckDB for ML pipeline migration"
        }
        
        import json
        with open(self.output_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info("📋 Metadata file created")
    
    def generate_summary(self):
        """Generate export summary"""
        logger.info("📊 Generating export summary...")
        
        total_size = 0
        file_count = 0
        
        for root, dirs, files in os.walk(self.output_dir):
            for file in files:
                if file.endswith('.parquet'):
                    file_path = os.path.join(root, file)
                    total_size += os.path.getsize(file_path)
                    file_count += 1
        
        size_mb = total_size / (1024 * 1024)
        size_gb = size_mb / 1024
        
        logger.info(f"✅ Export complete!")
        logger.info(f"📁 Files: {file_count}")
        logger.info(f"💾 Total size: {size_gb:.2f} GB ({size_mb:.0f} MB)")
        logger.info(f"📂 Output directory: {self.output_dir}")

def main():
    exporter = DuckDBParquetExporter()
    
    try:
        # Export options chains with DuckDB partitioning
        exporter.export_options_chains_duckdb()
        
        # Create serving schema
        exporter.create_serving_schema()
        
        # Verify export
        exporter.verify_parquet_export()
        
        # Create metadata
        exporter.create_metadata_file()
        
        logger.info("🎉 DuckDB migration complete! Ready for ML pipeline.")
        
    except Exception as e:
        logger.error(f"❌ Export failed: {e}")
        raise
    finally:
        exporter.close()

if __name__ == "__main__":
    main()
