# Documentation index

Start with the root [README](../README.md), then use the guides below for deeper implementation and operations details. Active documentation stays flat enough to remain searchable and easy to link; `archive/` is reserved only for intentionally historical documents whose old commands and paths may no longer match the live tree.

## Architecture and operations

| Document | When to read it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Understand the production data flow, services, API routes, quote pipeline, automation, and data limitations |
| [RAILWAY_SETUP.md](RAILWAY_SETUP.md) | Deploy FastAPI and the quote worker on Railway |
| [HMAC_PROXY.md](HMAC_PROXY.md) | Configure signed Vercel-to-Railway ML requests |
| [MODEL_CONTROL_PLANE.md](MODEL_CONTROL_PLANE.md) | Review signed model bundles, challenger promotion, drift monitoring, and automatic rollback |
| [DUCKDB_ARCHITECTURE.md](DUCKDB_ARCHITECTURE.md) | Review the offline Parquet/DuckDB analytical path and quote-quality views |
| [PROVIDER_SIGNAL_POLICY.md](PROVIDER_SIGNAL_POLICY.md) | Review paired-evidence gates for experimental vendor signals |
| [PERFORMANCE.md](PERFORMANCE.md) | Review frontend bottlenecks and prioritized fixes |
| [PRODUCTION_HARDENING_PLAN.md](PRODUCTION_HARDENING_PLAN.md) | Track the production hardening scope, rollout, and acceptance criteria |

## Scripts, data, and workers

| Document | When to read it |
|---|---|
| [../scripts/README.md](../scripts/README.md) | Run provider sync, scoring, R2, maintenance, and research commands |
| [../tools/README.md](../tools/README.md) | Generate frontend JSON, market caps, popularity weights, and understand tool test ownership |
| [../data/README.md](../data/README.md) | Distinguish operational tracked state from isolated research artifacts |
| [../workers/refresh-prices/README.md](../workers/refresh-prices/README.md) | Configure the Cloudflare Worker that triggers the Vercel quote cron |
| [R2_SETUP.md](R2_SETUP.md) | Configure R2 and rclone |

## Archive

Historical documents are retained for design provenance, not as runnable instructions. Prefer the active guides above for current paths and commands.

| Document | Why it is archived |
|---|---|
| [archive/DATA_STRATEGY.md](archive/DATA_STRATEGY.md) | Historical ML split notes; active training uses feature_engineering.py, model_trainer.py, and walk-forward research |
| [archive/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md](archive/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md) | Original provider roadmap; implemented behavior is documented in the architecture and worker guides |
