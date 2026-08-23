# Scripts

Python and Node utilities for **DoltHub → Parquet → DuckDB → scoring → Postgres → frontend JSON**. Root [`package.json`](../package.json) exposes most commands as `npm run data:*` / `npm run ml:*`.

See [`.github/workflows/daily-refresh.yml`](../.github/workflows/daily-refresh.yml) for production order.

## Inventory

| Script | Purpose | npm / CI |
|--------|---------|----------|
| `sync_dolthub.py` | Options, earnings, OHLCV, vol history from DoltHub | `data:sync*`, nightly |
| `sync_finnhub_earnings.py` | Near-term earnings overlay into `data/earnings_calendar.csv` | `data:earnings:finnhub`, nightly |
| `sync_fmp_earnings.py` | Date-window FMP EPS/revenue overlay into `data/earnings_calendar.csv` | `data:earnings:fmp`, nightly |
| `backfill_fmp_earnings_by_symbol.py` | One-symbol FMP EPS/revenue history research backfill | `data:earnings:fmp-backfill`, manually enabled only |
| `apply_earnings_overrides.py` | Re-applies `config/earnings_overrides.json` (manual date/timing/fiscal corrections) on top of provider syncs so they survive the nightly re-pull | `data:earnings:overrides`, nightly (after earnings syncs, before build) |
| `sync_finnhub_profiles.py` | Market-hours-guarded Finnhub profile/logo cache for `ticker-logos.json` | `data:profiles:finnhub`, weekly/manual |
| `sync_vix.py` | Authoritative CBOE VIX history → Parquet | Nightly |
| `probe_alphavantage_voi.py` | Alpha Vantage V/OI entitlement research | Manual research only |
| `probe_provider_capabilities.py` | Additive-provider entitlement/shape research | Manual research only |
| `sync_provider_enrichments.py` | Derived provider-signal research tables, production-policy gated | Manual research workflow |
| `detect_delistings.py` | Flags forecast-universe tickers gone from NASDAQ/NYSE directories; auto-adds confirmed delistings to `config/delisted_tickers.json` after N days (renames excluded) | Nightly (before integrity gate) |
| `delisted.py` | Loader for `config/delisted_tickers.json` + `config/ticker_renames.json` (delistings & old→new symbol remaps; shared by the gates + `sync_dolthub`) | import-only |
| `check_earnings_calendar_integrity.py` | Guardrails before committing calendar CSV (honors `delisted_tickers.json`) | Nightly (blocks commit on failure) |
| `check_ticker_identity.py` | Foreign-ticker leaks + Finnhub/SEC name alignment; bare-symbol logo cache | After `sync_finnhub_profiles` |
| `audit_logo_sources.py` | Per-ticker logo path audit for full `ticker-names.json` universe (no HTTP) | Manual / after logo changes |
| `setup_duckdb_from_parquet.py` | Recreate DuckDB views | `data:views`, nightly |
| `check_duckdb_freshness.py` | CI gate before scoring | Nightly |
| `daily_score.py` | ML forecasts for upcoming earnings | `ml:score`, nightly |
| `import_recent_to_postgres.py` | Push latest forecast rows to Neon (`--full` in CI) | Nightly |
| `data_healthcheck.py` | Local data quality checks | `ml:validate` |
| `csv_to_parquet.py` | CSV → Parquet | `data:csv-parquet` |
| `csv_to_parquet_volhist.py` | Vol history CSV → Parquet | Manual |
| `build_ticker_names.mjs` | SEC EDGAR → `ticker-names.json` + exchanges | Quarterly workflow |
| `migrate.mjs` | Watchlist DDL against Neon `DATABASE_URL` | Manual (not in CI) |
| `r2_pull.sh` / `r2_push.sh` / `r2_bootstrap.sh` | R2 sync | Nightly + [R2_SETUP.md](../docs/R2_SETUP.md) |
| `activate_model_bundle.py` | Exact-bundle Railway activation + receipt | Retrain/rollback handoff |

Frontend JSON build lives in [`tools/`](../tools/README.md) (`build_frontend_data.py`, `build_popular_weights.py`, `pull_market_caps.py`).

## ML experiments

Score against realized moves. The straddle is the simple baseline, not the
answer. Use [`research/experiment_model_improvements.py`](research/experiment_model_improvements.py):
same train/test splits for each change vs the baseline. Ship only if average
error drops and the drop is clearly bigger than noise. Confirm on a later
window with `--oos-offset`.

## Python environment

Scripts that import shared modules (`sync_finnhub_earnings`, pyarrow, etc.) expect the
repo virtualenv:

```bash
source .venv/bin/activate
# or: .venv/bin/python scripts/check_ticker_identity.py
```

If a dependency is missing: `.venv/bin/pip install <package>` (see root `requirements*.txt`).

## Typical local flow

```bash
source .venv/bin/activate
npm run data:sync
npm run data:earnings:finnhub -- --symbols AAPL,MSFT
npm run data:earnings:fmp-backfill -- --dry-run --max-calls 10
python scripts/check_earnings_calendar_integrity.py --warn-only   # optional locally
npm run data:views
npm run ml:score
npm run data:frontend
```

## FMP EPS/Revenue Backfill

`sync_fmp_earnings.py` uses one broad `/stable/earnings-calendar` request for
near-term rows. `backfill_fmp_earnings_by_symbol.py` handles the historical
path for FMP plans that allow `/stable/earnings?symbol=SYM`; that endpoint can
return many quarters for one symbol, but comma-separated symbols are not
available on the current plan.

The current free-tier key rejects `/stable/earnings?symbol=SYM` with a
plan-level 402, so the daily workflow keeps the symbol backfill behind
repository variable `ENABLE_FMP_SYMBOL_BACKFILL=1`. Leave that variable unset
until the FMP plan supports the symbol endpoint. When enabled, the script writes
progress to `data/fmp_earnings_backfill_state.json` and merges
fill-missing-only by default. Do not use this as a source of truth for earnings
dates until provider disagreements are explicitly reviewed; FMP-only dates are
skipped unless `--insert-new-events` is passed.
## Provider-signal research

Core production sources remain narrowly scoped: DoltHub options/earnings/OHLCV,
Finnhub near-term earnings and regular quotes, FMP EPS/revenue overlay, CBOE VIX,
SEC identity metadata, and the explicit quote fallbacks.

Additive vendor signals—options flow, short interest, supplemental fundamentals,
corporate actions, macro, live-market cross-checks, and news sentiment—are frozen
by [`config/provider_signal_policy.json`](../config/provider_signal_policy.json).
They do not run nightly, publish into frontend JSON, or enter an ML feature
schema until a pinned paired walk-forward report proves incremental value at no
additional monthly cost.

The `Manual provider-signal research` workflow runs
`sync_provider_enrichments.py --research-override` and uploads a 14-day artifact;
it never commits research output to `main`. A normal run without that explicit
override filters every endpoint through the production policy and leaves prior
research artifacts untouched when none are approved.

See [Provider signal promotion policy](../docs/PROVIDER_SIGNAL_POLICY.md) for the
evidence schema and promotion gates.

Manual dry-run examples:

```bash
npm run data:probe:providers -- --dry-run --max-calls 35
python scripts/sync_provider_enrichments.py \
  --research-override \
  --dry-run \
  --max-symbols 8 \
  --max-total-calls 60
```


## Env

`DATA_DIR`, `DUCKDB_PATH`, `DATABASE_URL` in **`config/.env.local`** — see [`.env.example`](../.env.example).

## Archive and research

Older one-off provider and retrain experiments live under `scripts/archive/`
and `scripts/research/`. Keep active CI entrypoints in the inventory above.

Research (not CI):

| Script | Purpose |
|--------|---------|
| `research/walk_forward.py` | Plot of average error over time (`npm run ml:walk-forward`) |
| `research/experiment_model_improvements.py` | Test whether a model change actually helps |
| `research/experiment_garch_feature.py` | Recent-weighted vol feature test |
| `research/experiment_retrain_cadence.py` | How often to retrain |
| `research/probe_signal_effectiveness.py` | Days-to-cover vs realized move |
| `research/backfill_analyst_dispersion.py` | Yearly analyst high–low range |
| `research/implied_pdf.py` | Up/down bands from implied vol by strike |

Nightly retrain still uses `scripts/validate_walk_forward.py`.
