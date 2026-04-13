# Scripts Directory

Core scripts for the Quantiv data and ML pipeline.

## Pipeline Overview

```
DoltHub (post-no-preference/options)
    │
    ├── sync_dolthub.py ──> Parquet (partitioned by year/month)
    │                              │
    │                              └── setup_duckdb_from_parquet.py ──> DuckDB views
    │                                         │
    │                                         ├── build_em_labels_features.py ──> ML features
    │                                         ├── train_baseline_models.py ──> LightGBM models
    │                                         └── daily_scoring_pipeline.py ──> Live predictions
    │
    └── setup_earnings_calendar.py ──> earnings_calendar.csv
```

## Quick Start

```bash
# 1. Initial data load from DoltHub (one-time, takes a while)
python scripts/sync_dolthub.py --full --start-date 2023-01-01

# 2. Set up DuckDB views over the Parquet files
python scripts/setup_duckdb_from_parquet.py

# 3. Build ML features and train models
python scripts/build_em_labels_features.py
python scripts/train_baseline_models.py

# 4. Generate predictions
python scripts/populate_ml_forecasts.py
```

## Daily Operations

```bash
# Incremental sync (only new rows since last sync)
python scripts/sync_dolthub.py

# Refresh DuckDB views
python scripts/setup_duckdb_from_parquet.py

# Score upcoming earnings
python scripts/daily_scoring_pipeline.py
```

## Scripts

### Data Pipeline (DoltHub → Parquet → DuckDB)
- `sync_dolthub.py` — Sync options chain data from DoltHub SQL API to Parquet
- `setup_duckdb_from_parquet.py` — Create DuckDB views over Parquet files

### ML Pipeline
- `setup_ml_pipeline.py` — Initialize ML pipeline
- `build_em_labels_features.py` — Build expected move features/labels
- `build_em_final.py` — Final feature engineering
- `train_baseline_models.py` — Train LightGBM models
- `populate_ml_forecasts.py` — Generate predictions

### Supporting
- `setup_earnings_calendar.py` — Fetch/update earnings calendar
- `data_healthcheck.py` — Validate data quality
- `daily_scoring_pipeline.py` — Daily production scoring

### Legacy (Postgres-based, kept for reference)
- `csv_to_postgres.py` — CSV → Postgres import
- `postgres_to_parquet.py` — Postgres → Parquet export
- `sync_postgres_to_parquet.py` — Incremental Postgres → Parquet sync

## NPM Scripts

```bash
npm run data:sync              # Incremental DoltHub sync
npm run data:sync-full         # Full DoltHub sync
npm run ml:setup               # Set up ML pipeline
npm run ml:forecast            # Generate forecasts
npm run ml:validate            # Health check
```

## Environment

Ensure `config/.env.local` contains:
```env
DATA_DIR=./data
DUCKDB_PATH=./data/quantiv.duckdb
```
