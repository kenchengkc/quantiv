# Scripts

Python and Node utilities for **DoltHub → Parquet → DuckDB → scoring → Postgres → frontend JSON**. Root [`package.json`](../package.json) exposes most commands as `npm run data:*` / `npm run ml:*`.

See [`.github/workflows/daily-refresh.yml`](../.github/workflows/daily-refresh.yml) for production order.

## Inventory

| Script | Purpose | npm / CI |
|--------|---------|----------|
| `sync_dolthub.py` | Options, earnings, OHLCV, vol history from DoltHub | `data:sync*`, nightly |
| `sync_finnhub_earnings.py` | Near-term earnings overlay into `data/earnings_calendar.csv` | `data:earnings:finnhub`, nightly |
| `sync_vix.py` | FRED VIX → Parquet | Nightly |
| `check_earnings_calendar_integrity.py` | Guardrails before committing calendar CSV | Nightly (blocks commit on failure) |
| `setup_duckdb_from_parquet.py` | Recreate DuckDB views | `data:views`, nightly |
| `check_duckdb_freshness.py` | CI gate before scoring | Nightly |
| `daily_score.py` | ML forecasts for upcoming earnings | `ml:score`, nightly |
| `import_recent_to_postgres.py` | Push latest forecast rows to Neon | Nightly |
| `data_healthcheck.py` | Local data quality checks | `ml:validate` |
| `csv_to_parquet.py` | CSV → Parquet | `data:csv-parquet` |
| `csv_to_parquet_volhist.py` | Vol history CSV → Parquet | Manual |
| `walk_forward.py` | Walk-forward validation (research) | `ml:walk-forward` |
| `build_ticker_names.mjs` | SEC EDGAR → `ticker-names.json` + exchanges | Quarterly workflow |
| `migrate.mjs` | Watchlist DDL against `DATABASE_URL` | Manual pre-deploy (not in CI) |
| `probe_massive_capabilities.py` | Massive.com API probe (Phase 0) | Manual |
| `r2_pull.sh` / `r2_push.sh` / `r2_bootstrap.sh` | R2 sync | Nightly + [r2_setup.md](r2_setup.md) |

Frontend JSON build lives in [`tools/`](../tools/README.md) (`build_frontend_data.py`, `build_popular_weights.py`, `pull_market_caps.py`).

## Typical local flow

```bash
npm run data:sync
npm run data:earnings:finnhub -- --symbols AAPL,MSFT
python scripts/check_earnings_calendar_integrity.py --warn-only   # optional locally
npm run data:views
npm run ml:score
npm run data:frontend
```

## Env

`DATA_DIR`, `DUCKDB_PATH`, `DATABASE_URL` in **`config/.env.local`** — see [`.env.example`](../.env.example).

## Provider roadmap

DoltHub is the historical baseline. Massive.com overlay evaluation: [`docs/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md`](../docs/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md), probe via `probe_massive_capabilities.py`.
