# Scripts

`scripts/` is Quantiv's operational command surface. Production entrypoints that are called directly by GitHub Actions or root `package.json` stay at the directory root so scheduled jobs have stable paths. Everything else is grouped by role.

See [`.github/workflows/daily-refresh.yml`](../.github/workflows/daily-refresh.yml) for the production execution order.

## Directory contract

```text
scripts/
├── *.py / *.mjs / *.sh   # stable production and operator entrypoints
├── maintenance/           # one-time setup, repair, migration, and conversion tools
├── provider_probes/       # manual provider entitlement/capability probes
├── research/              # experiments, backfills, and research diagnostics
└── tests/                 # pytest coverage for scripts and research utilities
```

Rules:

- Keep a script at the root only when Actions, npm, or another operational entrypoint calls that path directly, or when it is a shared module for those commands.
- Put human-run repair/setup/conversion utilities in `maintenance/`.
- Put provider evaluation probes in `provider_probes/`; production provider syncs remain at the root.
- Put research-only analysis and paused/manual research collectors in `research/`.
- Research-only outputs belong under `data/research/`, not beside production artifacts at the `data/` root.
- Put all script tests in `tests/`.
- Do not create a live code `archive/` directory. Git history is the archive for retired scripts.

The retired one-off Massive capability probe was removed because `provider_probes/probe_provider_capabilities.py` supersedes it.

## Common commands

Root [`package.json`](../package.json) exposes most routine commands:

```bash
npm run data:sync
npm run data:earnings:finnhub
npm run data:earnings:fmp
npm run data:views
npm run ml:score
npm run data:frontend
```

Manual provider research:

```bash
npm run data:probe:providers -- --dry-run --max-calls 35
npm run data:probe:alphavantage-voi -- --dry-run
python scripts/sync_provider_enrichments.py --research-override --dry-run --max-symbols 8
```

Maintenance examples:

```bash
node scripts/maintenance/migrate.mjs
python scripts/maintenance/csv_to_parquet_volhist.py
R2_BUCKET=quantiv-data bash scripts/maintenance/r2_bootstrap.sh
```

Research examples:

```bash
npm run ml:walk-forward
python scripts/research/experiment_model_improvements.py
python scripts/research/lookahead_audit.py --help
python scripts/research/accumulate_event_signals.py --help
```

## Operational root

The root contains the stable commands for ingestion, reconciliation, scoring, publication, model control, and artifact synchronization. Important entrypoints include:

- `sync_dolthub.py`, `sync_finnhub_earnings.py`, `sync_fmp_earnings.py`, `sync_vix.py`
- `detect_delistings.py`, `apply_ticker_lifecycle.py`, `check_earnings_calendar_integrity.py`
- `build_data_reconciliation.py`, `setup_duckdb_from_parquet.py`, `check_duckdb_freshness.py`
- `daily_score.py`, `validate_ml_pipeline.py`, `validate_walk_forward.py`
- `model_control_plane.py`, `package_model_bundle.py`, `activate_model_bundle.py`
- `import_recent_to_postgres.py`, `data_release.py`, `r2_pull.sh`, `r2_push.sh`
- shared modules such as `delisted.py`, `market_sessions.py`, `provider_utils.py`, `provider_specs.py`, and `provider_probe.py`

Keeping these paths stable avoids turning a directory cleanup into a production-workflow migration.

## Provider-signal research

Core production sources remain narrowly scoped. Additive vendor signals are frozen by [`config/provider_signal_policy.json`](../config/provider_signal_policy.json) and do not enter production until paired evidence clears the promotion gates.

The manual provider-signal workflow runs `sync_provider_enrichments.py --research-override`; provider entitlement and response-shape discovery belongs under `provider_probes/`. Persistent manual probe evidence and isolated research samples belong under [`data/research/`](../data/README.md).

See [Provider signal promotion policy](../docs/PROVIDER_SIGNAL_POLICY.md).

## Python environment and tests

Use the repo virtualenv:

```bash
source .venv/bin/activate
python -m pytest scripts tools -q
```

CI adds `scripts/` and `tools/` to `PYTHONPATH`; their local `tests/conftest.py` files provide equivalent import paths for local pytest collection.

Environment variables such as `DATA_DIR`, `DUCKDB_PATH`, and `DATABASE_URL` live in `config/.env.local`; see [`.env.example`](../.env.example).
