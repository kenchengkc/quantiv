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
| `r2_pull.sh` / `r2_push.sh` / `r2_bootstrap.sh` | R2 sync for CI / local |
| `build_ticker_names.mjs` | Fetches SEC EDGAR company tickers, normalizes casing, writes `apps/frontend/public/ticker-names.json` (consumed by the frontend `companyName()` helper as a fallback for non-S&P-500 names). Refresh a few times per year — SEC updates infrequently. Run with `node scripts/build_ticker_names.mjs`. |
| `probe_massive_capabilities.py` | **Phase 0 / read-only.** Hits Massive.com's REST option-chain snapshot endpoint with the user's Starter credentials and writes a schema + coverage report to `data/ref/provider_samples/massive/<date>/`. Does not modify any production data. Run once before writing the Massive overlay sync to confirm IV/Greeks coverage near ATM, pagination behavior, and `contract_type` value mapping. See `docs/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md` for pass/fail criteria. |

One-off R2 setup steps: **`r2_setup.md`**.

## Options-data provider strategy

DoltHub remains the **historical baseline** for options data. The repo is
in Phase 0 of evaluating Massive.com as a **forward-collection overlay**
(broader ticker coverage, fresher IV/Greeks for upcoming earnings) — not
a replacement. See `docs/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md` for the
full rollout plan; `probe_massive_capabilities.py` is the only script
added in this phase. Subsequent scripts (`sync_massive_snapshots.py`,
canonical-union DuckDB views, optional GitHub Actions step) are gated on
the probe results.

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
