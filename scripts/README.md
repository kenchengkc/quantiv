# Scripts Directory

This directory contains the core scripts for the Quantiv ML pipeline using a **Postgres-first workflow**.

## Core Workflow Scripts

### Data Import (CSV → PostgreSQL)
- `csv_to_postgres.py` - Import CSV data directly to PostgreSQL tables

### Data Pipeline (PostgreSQL → Parquet)
- `postgres_to_parquet.py` - Convert PostgreSQL data to partitioned Parquet files
- `sync_postgres_to_parquet.py` - Sync PostgreSQL to Parquet with incremental updates

### ML Pipeline
- `setup_ml_pipeline.py` - Initialize ML pipeline and DuckDB views
- `build_em_labels_features.py` - Build features for expected move predictions
- `build_em_final.py` - Final feature engineering and model preparation
- `train_baseline_models.py` - Train ML models for expected move forecasting
- `populate_ml_forecasts.py` - Generate ML predictions and populate forecast tables

### Data Management
- `setup_data_structure.py` - Initialize database schemas and tables
- `setup_duckdb_views.py` - Create DuckDB views for ML pipeline
- `setup_earnings_calendar.py` - Setup earnings calendar data
- `data_healthcheck.py` - Validate data quality and ML pipeline health

### Production Pipeline
- `daily_scoring_pipeline.py` - Daily production scoring for live predictions

## Workflow Overview

```
CSV Files → PostgreSQL → Parquet Files → DuckDB Views → ML Models → Predictions
```

### 1. Data Import
```bash
# Import options chain CSV to PostgreSQL
CSV_FILE=data/options_chain.csv python scripts/csv_to_postgres.py

# Import volatility history CSV to PostgreSQL  
CSV_FILE=data/volatility_history.csv python scripts/csv_to_postgres.py
```

### 2. Data Sync to Parquet
```bash
# Full sync from PostgreSQL to Parquet
python scripts/sync_postgres_to_parquet.py --local

# Incremental sync (last 7 days)
python scripts/sync_postgres_to_parquet.py --local --incremental --days 7
```

### 3. ML Pipeline Setup
```bash
# Initialize ML pipeline
python scripts/setup_ml_pipeline.py --local

# Build features and train models
python scripts/build_em_labels_features.py --local
python scripts/train_baseline_models.py --local

# Generate predictions
python scripts/populate_ml_forecasts.py --local
```

### 4. Health Check
```bash
# Validate entire pipeline
python scripts/data_healthcheck.py --local
```

## NPM Scripts

Convenient npm scripts are available in `package.json`:

```bash
# ML pipeline
npm run ml:setup
npm run ml:forecast
npm run ml:validate

# Data sync
npm run data:sync
npm run data:sync-incremental
```

## Environment Setup

Ensure `config/.env.local` contains:

```env
# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=quantiv_user
POSTGRES_PASSWORD=quantiv_secure_2024
POSTGRES_DB=quantiv_options

# Data paths
DATA_DIR=/path/to/quantiv/data
DUCKDB_PATH=/path/to/quantiv/data/quantiv.duckdb
```
