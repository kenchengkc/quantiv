# Tools

Python utilities that **build or enrich frontend artifacts** and supporting
datasets. Invoked from CI ([`.github/workflows/daily-refresh.yml`](../.github/workflows/daily-refresh.yml))
or locally via `npm run data:frontend` (which calls `build_frontend_data.py`).

| Script | Purpose | CI |
|--------|---------|-----|
| `build_frontend_data.py` | Writes `apps/frontend/public/{weekly,weeks,symbols,screener}.json`; can use quota-managed TwelveData daily closes as a backend-only realized-move fallback | Nightly |
| `twelvedata_basic.py` | Basic-tier quota ledger + `/time_series` daily-close helper used by `build_frontend_data.py` | Helper |
| `build_popular_weights.py` | Regenerates popular-ticker weights consumed by the screener filter | Nightly |
| `pull_market_caps.py` | Finnhub market-cap snapshot for ranking / display; merges top-profile logos into `ticker-logos.json` | Nightly |
| `build_earnings_events.py` | Legacy/helper earnings event builder — run manually if needed | No |
| `patch_timing.py` | One-off timing patches on calendar data | Manual |
| `math_baseline.py` | EM baseline math experiments | Manual |

Env: `DATA_DIR`, `DUCKDB_PATH` in `config/.env.local` (see [`.env.example`](../.env.example)).
TwelveData fallback requires `TWELVEDATA_API_KEY` in the environment that runs
`build_frontend_data.py` (GitHub Actions for production). Optional tuning env:
`TWELVEDATA_DAILY_CREDIT_LIMIT` (default `792`), `TWELVEDATA_BATCH_SIZE`
(default `8`), `TWELVEDATA_BATCH_DELAY_SEC` (default `61`), and
`TWELVEDATA_LEDGER_PATH` (default `data/twelvedata_usage_ledger.json`).
By default, TwelveData credits are also mirrored into the shared provider ledger
(`data/provider_usage_ledger.json`) so press-release/enrichment usage and
realized-move fallback usage share one daily cap. Set
`TWELVEDATA_SHARE_PROVIDER_LEDGER=0` only for isolated local tests.
After realized moves and historical averages, leftover credits may run a small
local-vs-TwelveData validation sample controlled by
`TWELVEDATA_VALIDATION_SAMPLE_SIZE` (default `8`) and
`TWELVEDATA_VALIDATION_DELTA_PCT` (default `0.005`).
Preview planned credits without spending them:
`python tools/build_frontend_data.py --twelvedata-dry-run`.
