# DuckDB + Parquet Architecture (Quantiv)

This document defines the canonical storage conventions, DuckDB views, and integration points used by Quantiv after migrating historical data from PostgreSQL to Parquet + DuckDB.

## Goals
- Keep historical data in columnar Parquet with predictable partitions and filenames.
- Query via DuckDB views to enforce stable schemas and types.
- Preserve API contracts while enabling a hybrid backend (DuckDB for history, Postgres/live for most-recent data).

## Storage Conventions
- Root: `data/`
- Compression: Snappy (canonical for new exports). Legacy exports may use ZSTD.
- File format: Parquet (pyarrow)
- Timezone: All timestamps stored as UTC where applicable.

### Layout
```
data/
├── options/
│   └── year=YYYY/
│       └── month=MM/
│           └── options_YYYY_MM.parquet
├── volatility/
│   └── year=YYYY/
│       └── volatility_YYYY.parquet
├── forecasts/
│   ├── em_forecasts.parquet
│   ├── atm_features.parquet
│   └── em_labels.parquet
└── metadata/
    ├── symbols_metadata.parquet
    ├── model_meta.parquet
    └── model_performance.parquet
```

### Partitioning Rules
- Options: Hive-style `year`, `month` from quote `date`.
- Volatility: Hive-style `year` from `date`.
- Forecasts/Metadata: No partitioning (single files), may evolve to partitioned if volumes grow.

### Naming Rules
- Options: `options_YYYY_MM.parquet` under `year=YYYY/month=MM/`
- Volatility: `volatility_YYYY.parquet` under `year=YYYY/`
- Forecasts/Metadata: `<table>.parquet` under respective folders.

## Schema and Types
DuckDB views cast Parquet columns to stable names and types. Current mapping reflects existing files where some columns are stored with numeric names.

### Options schema mapping (view `options_chain`)
- id BIGINT ← "0"
- date DATE ← "1"
- act_symbol VARCHAR ← "2"
- expiration DATE ← "3"
- strike DOUBLE ← "4"
- call_put VARCHAR ← "5"
- bid DOUBLE ← "6"
- ask DOUBLE ← "7"
- vol DOUBLE ← "8"
- delta DOUBLE ← "9"
- gamma DOUBLE ← "10"
- theta DOUBLE ← "11"
- vega DOUBLE ← "12"
- open_interest BIGINT ← "13"
- volume BIGINT ← "14"
- created_at TIMESTAMP ← "15"
- Partition columns available in view: `partition_year`, `partition_month` (INTEGER)

### Volatility schema mapping (view `volatility_history`)
- id BIGINT ← "0"
- date DATE ← "1"
- symbol VARCHAR ← "2"
- iv DOUBLE ← "3"
- hv DOUBLE ← "4"
- iv_rank DOUBLE ← "5"
- iv_percentile DOUBLE ← "6"
- created_at TIMESTAMP ← "7"
- Partition column in view: `partition_year` (INTEGER)

Notes:
- Exporter was updated to preserve column names when constructing DataFrames. Existing files may still have numeric column names; the views normalize these.
- Dates use DuckDB DATE; timestamps use TIMESTAMP (UTC).

## DuckDB Database and Views
- Database file: `quantiv.duckdb`
- Created by: `migration/setup_duckdb.py`
- Extensions: `parquet`, `httpfs`
- Important settings: `memory_limit`, `threads`, `temp_directory`

### Core objects
- Views from Parquet:
  - `options_chain` ← `data/options/**/options_*.parquet` (hive_partitioning, union_by_name)
  - `volatility_history` ← `data/volatility/**/volatility_*.parquet`
  - ML/Metadata views (if files present): `em_forecasts`, `atm_features`, `em_labels`, `symbols_metadata`, `model_meta`, `model_performance`
- Analytical views:
  - `daily_iv_summary` (avg/median IV by `date`, `act_symbol`)
  - `atm_options` (near-average strike per symbol/day)
  - `symbol_summary` (per-symbol stats)
- Materialized tables:
  - `recent_options` (last 30 trading days from `options_chain`)
  - `symbols_metadata_computed` (from `symbol_summary`)

### Regeneration runbook
```
python3 migration/setup_duckdb.py --data-dir ./data --db-file ./quantiv.duckdb
```
- Creates/loads extensions, sets DB settings, creates views/tables, runs light test queries, and exports schema info to `duckdb_schema.txt`.

## Backend Integration
- Env vars (see `config/.env.example`):
  - `DATA_DIR` (default `./data`)
  - `DUCKDB_PATH` (default `./quantiv.duckdb`)
  - `DATA_BACKEND` = `postgres` | `duckdb` | `hybrid`
- Backend abstraction implemented in `apps/backend/main.py` selects the active backend at startup and routes reads through DuckDB/Postgres/hybrid accordingly.

## Performance & Quality
- DuckDB views use `hive_partitioning=true` and `union_by_name=true` for robust schema handling.
- Observed coverage (as of latest snapshot):
  - `options_chain`: 85,706,146 rows (2020-01-04 → 2025-08-14)
  - `volatility_history`: 1,509,260 rows (2019-02-09 → 2025-08-14)
- Compression: Snappy for new exports; legacy `data/parquet/options_chains` is ZSTD and not required for production.

## Exporter Guidelines
- Script: `migration/export_to_parquet.py`
- Defaults: Snappy compression, partitioning by year/month (options) and year (volatility)
- Recommended: re-run exports that still have numeric column names when convenient; otherwise rely on DuckDB view casts.

## Future-proofing
- If ML volumes grow, adopt partitioning for forecasts by `horizon` and/or `quote_year`.
- Add periodic compaction (coalesce small files) if write patterns produce many tiny Parquet files.
- Maintain a schema registry (lightweight JSON) if needed for strong validation across pipelines.
