# Scripts

Python and Node utilities for **DoltHub → Parquet → DuckDB → scoring → Postgres → frontend JSON**. Root [`package.json`](../package.json) exposes most commands as `npm run data:*` / `npm run ml:*`.

See [`.github/workflows/daily-refresh.yml`](../.github/workflows/daily-refresh.yml) for production order.

## Inventory

| Script | Purpose | npm / CI |
|--------|---------|----------|
| `sync_dolthub.py` | Options, earnings, OHLCV, vol history from DoltHub | `data:sync*`, nightly |
| `sync_finnhub_earnings.py` | Near-term earnings overlay into `data/earnings_calendar.csv` | `data:earnings:finnhub`, nightly |
| `sync_fmp_earnings.py` | Date-window FMP EPS/revenue overlay into `data/earnings_calendar.csv` | `data:earnings:fmp`, nightly |
| `backfill_fmp_earnings_by_symbol.py` | Free-tier-safe one-symbol FMP EPS/revenue history backfill | `data:earnings:fmp-backfill`, nightly |
| `sync_finnhub_profiles.py` | Market-hours-guarded Finnhub profile/logo cache for `ticker-logos.json` | `data:profiles:finnhub`, weekly/manual |
| `sync_vix.py` | FRED VIX → Parquet | Nightly |
| `probe_alphavantage_voi.py` | Persistent multi-day Alpha Vantage V/OI entitlement and coverage audit | `data:probe:alphavantage-voi`, nightly |
| `probe_provider_capabilities.py` | Quota-managed entitlement/shape probes for FMP, Alpha Vantage, Massive/Polygon, and TwelveData additive endpoints | `data:probe:providers`, nightly |
| `sync_provider_enrichments.py` | Backend-only derived enrichment tables: news signals, company facts, options provider signals, corporate actions | `data:providers:enrich`, nightly |
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
| `walk_forward.py` | Walk-forward validation (research) | `ml:walk-forward` |
| `build_ticker_names.mjs` | SEC EDGAR → `ticker-names.json` + exchanges | Quarterly workflow |
| `migrate.mjs` | Watchlist DDL against `DATABASE_URL` | Manual pre-deploy (not in CI) |
| `probe_massive_capabilities.py` | Massive.com API probe (Phase 0) | Manual |
| `r2_pull.sh` / `r2_push.sh` / `r2_bootstrap.sh` | R2 sync | Nightly + [r2_setup.md](r2_setup.md) |

Frontend JSON build lives in [`tools/`](../tools/README.md) (`build_frontend_data.py`, `build_popular_weights.py`, `pull_market_caps.py`).

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

## Provider Enrichments

The additive provider layer is intentionally non-redundant with local OHLCV:

- FMP: earnings EPS/revenue overlay plus fundamentals, analyst estimates
  (with low/high dispersion), ratings, and the single retained press-release
  news feed.
- Alpha Vantage: scarce 25/day-per-key budget reserved for the unique numeric
  option-flow endpoints (historical/realtime put-call and volume-to-OI ratios),
  earnings validation, and the IPO calendar. Macro series (treasury/fed-funds/
  CPI) moved to a `monthly` cadence; news sentiment retired in favor of FMP.
- Massive/Polygon: uses the existing `POLYGON_API_KEY`; primary external source
  for current option-chain snapshots, short interest, splits, dividends,
  financials, and only narrow OHLCV gap fills. Ticker overview is a `weekly`
  profile cross-check (Finnhub owns the profile role).
- TwelveData: tertiary realized-move OHLCV fallback plus Basic-tier
  `last_change` and technical-indicator validation. Quote/52-week context is a
  `weekly` cross-check; press releases retired in favor of FMP.

Each endpoint carries a `cadence` (`daily`/`weekly`/`monthly`/`off`) in
`provider_specs.py`. The nightly run is `--cadences daily`; schedule
`weekly`/`monthly` on matching days to refresh slow-changing reference and macro
data. `off` endpoints stay in the catalog for manual/probe use but never
auto-run.

API key-pool stacking: set numbered key variants (e.g. `ALPHAVANTAGE_API_KEY_2`,
`..._3`) to multiply a provider's free-tier budget. The usage ledger accounts
for each key separately and fails over when one is exhausted — highest value for
Alpha Vantage's 25/day-per-key cap.

`probe_provider_capabilities.py` writes `data/provider_capabilities.json` with
status and response-shape metadata only. `sync_provider_enrichments.py` then
uses only endpoints that probed `ok` and writes derived backend-only JSON tables
under `data/provider_enrichments/`. Raw provider payloads are not persisted or
published in frontend JSON.

Quota defaults are conservative: `FMP_DAILY_CALL_LIMIT=225`,
`ALPHAVANTAGE_DAILY_CALL_LIMIT=25`, `TWELVEDATA_DAILY_CREDIT_LIMIT=792`, and
`MASSIVE_MINUTE_CALL_LIMIT=5`. The nightly workflow lowers the new enrichment
budgets to leave room for existing FMP, Alpha Vantage, and TwelveData jobs.

Dry-run examples:

```bash
npm run data:probe:providers -- --dry-run --max-calls 35
npm run data:providers:enrich -- --dry-run --max-symbols 8 --max-total-calls 60
```

## Env

`DATA_DIR`, `DUCKDB_PATH`, `DATABASE_URL` in **`config/.env.local`** — see [`.env.example`](../.env.example).

## Provider roadmap

DoltHub is the historical baseline. Massive.com overlay evaluation: [`docs/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md`](../docs/EXTENDED_HOURS_AND_OPTIONS_DATA_PLAN.md), probe via `probe_massive_capabilities.py`. Massive/Polygon uses `POLYGON_API_KEY`; `MASSIVE_API_KEY` is only accepted as a local compatibility alias.
