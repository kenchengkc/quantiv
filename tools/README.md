# Tools

Python utilities that **build or enrich frontend artifacts** and supporting
datasets. Invoked from CI ([`.github/workflows/daily-refresh.yml`](../.github/workflows/daily-refresh.yml))
or locally via `npm run data:frontend` (which calls `build_frontend_data.py`).

| Script | Purpose | CI |
|--------|---------|-----|
| `build_frontend_data.py` | Writes `apps/frontend/public/{weekly,weeks,symbols,screener}.json` | Nightly |
| `build_popular_weights.py` | Regenerates popular-ticker weights consumed by the screener filter | Nightly |
| `pull_market_caps.py` | Finnhub market-cap snapshot for ranking / display | Nightly |
| `build_earnings_events.py` | Legacy/helper earnings event builder — run manually if needed | No |
| `patch_timing.py` | One-off timing patches on calendar data | Manual |
| `math_baseline.py` | EM baseline math experiments | Manual |

Env: `DATA_DIR`, `DUCKDB_PATH` in `config/.env.local` (see [`.env.example`](../.env.example)).
