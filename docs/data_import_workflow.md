# Data Import Workflow

This document explains how to import new CSV data for options_chain and volatility_history into the Quantiv platform using a **Postgres-first workflow** and update the ML pipeline.

## Overview

The data flow follows this **Postgres-first** pattern:
```
CSV Files → PostgreSQL → Parquet Files → ML Pipeline → Predictions
```

This ensures data safety and integrity by using PostgreSQL as the source of truth before converting to Parquet for ML processing.

## Quick Start

### 1. Import New CSV Data to PostgreSQL

For **options_chain** data:
```bash
CSV_FILE=path/to/new_options_data.csv python scripts/csv_to_postgres.py
```

For **volatility_history** data:
```bash
CSV_FILE=path/to/new_volatility_data.csv python scripts/csv_to_postgres.py
```

### 2. Sync PostgreSQL to Parquet Files

After importing CSV data to PostgreSQL, sync to Parquet files:
```bash
# Full sync from PostgreSQL to Parquet
npm run data:sync

# Or incremental sync (last 7 days only)
npm run data:sync-incremental
```

### 3. Update ML Pipeline

After syncing to Parquet, update the ML pipeline:
```bash
# Rebuild features and retrain models
npm run ml:setup

# Generate new forecasts
npm run ml:forecast
```

### 4. Verify Import

Check data health and ML predictions:
```bash
npm run ml:validate
```

## Detailed Workflow

### CSV File Requirements

#### Options Chain CSV Format
Required columns:
- `act_symbol` - Stock symbol (e.g., AAPL)
- `date` - Trade date (YYYY-MM-DD)
- `expiry` - Option expiry date (YYYY-MM-DD)
- `strike` - Strike price (numeric)
- `call_put` - Option type (C/P or CALL/PUT)
- `bid` - Bid price (numeric)
- `ask` - Ask price (numeric)
- `last` - Last trade price (numeric)
- `vol` - Implied volatility (decimal, e.g., 0.25 for 25%)
- `open_interest` - Open interest (integer)
- `delta` - Delta (decimal)
- `gamma` - Gamma (decimal)
- `theta` - Theta (decimal)
- `vega` - Vega (decimal)
- `rho` - Rho (decimal)
- `iv` - Implied volatility (decimal)

#### Volatility History CSV Format
Required columns:
- `act_symbol` - Stock symbol (e.g., AAPL)
- `date` - Date (YYYY-MM-DD)
- `iv_current` - Current implied volatility (decimal)
- `hv_current` - Current historical volatility (decimal)
- `iv_week_ago` - IV from 1 week ago (decimal)
- `iv_month_ago` - IV from 1 month ago (decimal)
- `iv_year_high` - 1-year IV high (decimal)
- `iv_year_low` - 1-year IV low (decimal)

Optional columns:
- `iv_percentile` - IV percentile (0-1)
- `iv_rank` - IV rank (0-100)

### Data Processing Steps

1. **Validation**: CSV structure and required columns are validated
2. **Cleaning**: Data types are converted, invalid rows removed
3. **PostgreSQL Insert**: Data is inserted/upserted into PostgreSQL tables
4. **Parquet Sync**: PostgreSQL data is synced to partitioned Parquet files
5. **ML Pipeline Update**: Features are rebuilt and models retrained

### Advanced Usage

#### Incremental Data Sync

For regular updates, use incremental sync to only process recent data:
```bash
# Sync last 7 days of data from Postgres to Parquet
npm run data:sync-incremental

# Sync last 30 days
python scripts/sync_postgres_to_parquet.py --local --incremental --days 30
```

#### Full Data Sync

To rebuild all Parquet files from PostgreSQL:
```bash
npm run data:sync
```

#### Environment Configuration

Configure PostgreSQL connection in `config/.env.local`:
```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=quantiv_user
POSTGRES_PASSWORD=quantiv_secure_2024
POSTGRES_DB=quantiv_options
DATABASE_URL=postgresql://quantiv_user:quantiv_secure_2024@localhost:5432/quantiv_options
```

## Database Schema

### Options Chain Table
```sql
CREATE TABLE options_chain (
    act_symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    expiry DATE NOT NULL,
    strike DECIMAL(10,2) NOT NULL,
    call_put CHAR(1) NOT NULL CHECK (call_put IN ('C', 'P')),
    bid DECIMAL(10,4),
    ask DECIMAL(10,4),
    last DECIMAL(10,4),
    vol DECIMAL(8,6),
    open_interest INTEGER,
    delta DECIMAL(8,6),
    gamma DECIMAL(8,6),
    theta DECIMAL(8,6),
    vega DECIMAL(8,6),
    rho DECIMAL(8,6),
    iv DECIMAL(8,6),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (act_symbol, date, expiry, strike, call_put)
);
```

### Volatility History Table
```sql
CREATE TABLE volatility_history (
    act_symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    iv_current DECIMAL(8,6),
    hv_current DECIMAL(8,6),
    iv_week_ago DECIMAL(8,6),
    iv_month_ago DECIMAL(8,6),
    iv_year_high DECIMAL(8,6),
    iv_year_low DECIMAL(8,6),
    iv_percentile DECIMAL(5,4),
    iv_rank DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (act_symbol, date)
);
```

## Parquet File Structure

Data is partitioned by symbol, year, and month:
```
data/parquet/
├── options_chain/
│   └── underlying=AAPL/
│       └── quote_year=2024/
│           └── quote_month=01/
│               └── options_chain_AAPL_2024_01.parquet
└── volatility_history/
    └── underlying=AAPL/
        └── quote_year=2024/
            └── quote_month=01/
                └── volatility_history_AAPL_2024_01.parquet
```

## Troubleshooting

### Common Issues

1. **CSV Validation Errors**
   - Check column names match exactly (case-sensitive)
   - Ensure date formats are YYYY-MM-DD
   - Verify numeric columns contain valid numbers

2. **PostgreSQL Connection Errors**
   - Check `config/.env.local` database settings
   - Ensure PostgreSQL is running
   - Verify credentials and database exists

3. **Parquet File Errors**
   - Check disk space availability
   - Ensure write permissions to data directory
   - Verify pyarrow is installed

4. **ML Pipeline Errors**
   - Run data validation: `npm run ml:validate`
   - Check DuckDB file exists: `data/quantiv.duckdb`
   - Verify sufficient training data (>50 samples)

### Performance Tips

- Use incremental sync for regular updates
- Import data in batches for large datasets
- Monitor disk space for Parquet files
- Run ML pipeline updates during off-peak hours

## Example Workflow

Complete workflow for adding new data using the **Postgres-first approach**:

```bash
# 1. Import new options data to PostgreSQL
CSV_FILE=/path/to/new_options.csv \
FINAL_TABLE=options_chain \
python scripts/csv_to_postgres.py

# 2. Import new volatility data to PostgreSQL  
CSV_FILE=/path/to/new_volatility.csv \
FINAL_TABLE=volatility_history \
python scripts/csv_to_postgres.py

# 3. Sync PostgreSQL data to Parquet files
npm run data:sync

# 4. Rebuild ML pipeline with new data
npm run ml:setup

# 5. Generate fresh predictions
npm run ml:forecast

# 6. Verify data and predictions
npm run ml:validate

# 7. Start services to see predictions
npm run dev:backend &
npm run dev:frontend
```

Visit http://localhost:3000 to see the updated ML predictions in the frontend.
