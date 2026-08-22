# Documentation index

Start with the root [README](../README.md), then use the guides below for deeper implementation and operations details.

## Architecture and operations

| Document | When to read it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Understand the production data flow, services, API routes, quote pipeline, automation, and data limitations |
| [RAILWAY_SETUP.md](RAILWAY_SETUP.md) | Deploy FastAPI and the quote worker on Railway |
| [HMAC_PROXY.md](HMAC_PROXY.md) | Configure signed Vercel-to-Railway ML requests |
| [duckdb_architecture.md](duckdb_architecture.md) | Review Parquet layout, DuckDB views, and `DATA_BACKEND` modes |
| [PERFORMANCE.md](PERFORMANCE.md) | Review frontend bottlenecks and prioritized fixes |
| [PRODUCTION_HARDENING_PLAN.md](PRODUCTION_HARDENING_PLAN.md) | Track the production hardening scope, rollout, and acceptance criteria |

## Scripts and workers

| Document | When to read it |
|---|---|
| [../scripts/README.md](../scripts/README.md) | Run provider sync, scoring, R2, and CI pipeline steps |
| [../tools/README.md](../tools/README.md) | Generate frontend JSON, market caps, and popularity weights |
| [../workers/refresh-prices/README.md](../workers/refresh-prices/README.md) | Configure the Cloudflare Worker that triggers the Vercel quote cron |
| [../scripts/r2_setup.md](../scripts/r2_setup.md) | Configure R2 and rclone |

## Archive

| Document | Why it is archived |
|---|---|
| [archive/DATA_STRATEGY.md](archive/DATA_STRATEGY.md) | Historical ML split notes; active training uses the v3 pipeline and walk-forward research |
| [archive/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md](archive/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md) | Original provider roadmap; implemented behavior is documented in the architecture and worker guides |
