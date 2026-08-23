# DuckDB and Parquet architecture

DuckDB is Quantiv's offline analytical engine. It reads local Parquet during
scheduled feature engineering, validation, scoring, reconciliation, and static
JSON generation. It is not a production FastAPI serving mode.

## Production boundary

```text
R2 immutable Parquet release
→ local data/parquet files in GitHub Actions
→ DuckDB views and quality quarantine
→ LightGBM scoring + frontend artifact build
→ static JSON on Vercel

Neon feature_vector rows
→ Railway POST /api/ml/predict
```

There is no `DATA_BACKEND` selector and no Postgres/DuckDB/hybrid backend
abstraction. Railway reads current forecast feature vectors from Neon and loads
signed native LightGBM bundles from its volume. Historical analytical queries
stay in the scheduled/local DuckDB process.

## Canonical layout

```text
data/
├── parquet/
│   ├── options_chain/year=YYYY/month=MM/*.parquet
│   ├── ohlcv/year=YYYY/month=MM/*.parquet
│   ├── volatility_history/year=YYYY/month=MM/*.parquet
│   └── vix/vix.parquet
├── earnings_calendar.csv
├── earnings_calendar.parquet
├── forecasts/forecasts_YYYY-MM-DD.parquet
├── ml_training/training_T{horizon}.parquet
├── models/
│   ├── bundles/<sha256>/
│   ├── control/
│   └── monitoring/
└── control/
    ├── releases/<release-id>.json
    └── current_data_release.json
```

The data-release pointer is signed and promoted only after its immutable
Parquet members have uploaded and verified. Model bundles use a separate signed
champion pointer.

## Views and controls

`scripts/setup_duckdb_from_parquet.py` creates the current analytical contract:

- `v_options_raw`: provider-shaped options rows from Parquet;
- `v_options`: typed options fields plus quote-quality status;
- `v_option_quote_quarantine`: crossed, nonpositive, stale, excessive-spread,
  or otherwise unusable contracts;
- `v_straddle_candidates`: synchronized call/put pairs and pair-level quality;
- `v_straddle_quote_quarantine`: commercially unusable pairs;
- `v_atm_options`: ATM legs selected only from eligible pairs;
- `v_straddle_features`: straddle, ATM IV, skew, term, and quote evidence;
- `v_earnings`: canonical earnings events;
- `v_volhist_raw`: normalized vendor volatility history;
- `v_vix`: authoritative CBOE VIX history;
- `v_ohlcv`, `v_realized_vol`, and `v_iv_rv_features`: realized-volatility and
  IV-versus-realized features when OHLCV is present.

The same paired options view feeds the math baseline and ML feature extraction,
so publication cannot silently use a looser quote-quality definition than
training. Reconciliation and pipeline-validation scripts turn critical
exceptions into mandatory workflow failures.

## Rebuild and validate

```bash
python scripts/setup_duckdb_from_parquet.py \
  --data-dir ./data \
  --db-file ./quantiv.duckdb
python scripts/check_duckdb_freshness.py
python scripts/reconcile_data_pipeline.py
```

The frontend artifact entrypoint is `tools/build_frontend_data.py`. Its small
orchestrator delegates forecast/provider inputs, timing-aware realized moves,
payload construction, and publication to `tools/frontend_data/` modules.

## Operating principles

- Parquet members are immutable within a signed data release.
- New schemas use explicit column names and Snappy compression.
- DuckDB views normalize legacy provider columns at the analytical boundary.
- Quote quarantine is preserved as evidence; rejected rows never enter ATM or
  straddle selection.
- Research scripts may read these views, but they stay outside the nightly job
  unless a tested publication control explicitly promotes them.
