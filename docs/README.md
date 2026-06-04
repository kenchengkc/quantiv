# Documentation index

Start with the root [README.md](../README.md), then use the guides below.

## Operations & architecture

| Doc | When to read |
|-----|----------------|
| [RAILWAY_SETUP.md](RAILWAY_SETUP.md) | Deploy FastAPI on Railway (`api.usequantiv.com`, volume, env) |
| [duckdb_architecture.md](duckdb_architecture.md) | Parquet layout, DuckDB views, `DATA_BACKEND` modes |
| [PERFORMANCE.md](PERFORMANCE.md) | Why pages feel slow and prioritized fixes |
| [HMAC_PROXY.md](HMAC_PROXY.md) | Vercel ↔ Railway signed proxy (`/api/ml/predict`) |

## Archive

| Doc | Why archived |
|-----|--------------|
| [archive/DATA_STRATEGY.md](archive/DATA_STRATEGY.md) | Historical ML split notes; active training is v3 + walk-forward |
| [archive/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md](archive/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md) | Original quote/provider roadmap; implemented runbook lives in scripts/workers docs |

## Scripts & workers

| Doc | When to read |
|-----|----------------|
| [../scripts/README.md](../scripts/README.md) | Python sync, scoring, R2, CI order |
| [../tools/README.md](../tools/README.md) | `build_frontend_data`, market caps, popular weights |
| [../workers/refresh-prices/README.md](../workers/refresh-prices/README.md) | Cloudflare Worker → Vercel quote cron |
| [../scripts/r2_setup.md](../scripts/r2_setup.md) | One-time R2 / rclone setup |
