# Scripts

Python utilities for **DoltHub → Parquet → DuckDB → scoring → frontend JSON**. Root **package.json** exposes most of these as `npm run data:*` / `npm run ml:*`.

## What’s in this folder

| Script | Purpose |
|--------|---------|
| `sync_dolthub.py` | Incremental / full sync of options, `--earnings`, `--ohlcv`, `--volhist` |
| `sync_vix.py` | VIX daily closes from FRED → Parquet |
| `setup_duckdb_from_parquet.py` | Recreate DuckDB views over Parquet (`npm run data:views`) |
| `daily_score.py` | ML scoring for upcoming earnings (`npm run ml:score`) |
| `check_duckdb_freshness.py` | CI gate before scoring |
| `data_healthcheck.py` | Data quality checks (`npm run ml:validate`) |
| `csv_to_parquet.py` | CSV → Parquet helper (`npm run data:csv-parquet`) |
| `csv_to_parquet_volhist.py` | Vol history CSV → Parquet |
| `refresh_prices.mjs` | Standalone Finnhub refresh (see `.github/workflows/refresh-prices.yml`) |
| `r2_pull.sh` / `r2_push.sh` / `r2_bootstrap.sh` | R2 sync for CI / local |

One-off R2 setup steps: **`r2_setup.md`**.

## ML training (not in `scripts/`)

Feature engineering and training live under **`apps/ml/`** (`feature_engineering_v3.py`, `model_trainer_v3.py`, etc.). See repo **README.md** ML section and **`apps/ml/`** for notebooks-style flows.

## Typical local flow

```bash
npm run data:sync
npm run data:views
npm run data:frontend
```

Add flags or `sync_vix.py` as needed; match **`.github/workflows/daily-refresh.yml`** for production order.

## Env

`DATA_DIR`, `DUCKDB_PATH` in **`config/.env.local`** (see repo **`.env.example`**).
