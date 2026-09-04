# How a Quantiv number reaches the UI

This guide is the shortest path through Quantiv for an unfamiliar engineer or researcher. It follows the three numbers most likely to be questioned during due diligence:

1. the market-implied earnings move;
2. the ML expected-move estimate;
3. the stock quote shown next to them.

The important architectural rule is that these numbers do **not** share one freshness boundary. Market/ML research is validated end-of-day evidence; stock quotes can be fresher. The UI must preserve that distinction.

## System map

```text
                           NIGHTLY / OFFLINE RESEARCH PATH

DoltHub · Finnhub · FMP · CBOE · SEC
                 │
                 ▼
       normalized CSV / Parquet
                 │
                 ▼
       reconciliation + quarantine
                 │
                 ▼
              DuckDB
                 │
       ┌─────────┴─────────┐
       │                   │
       ▼                   ▼
straddle / IV features   ML features
       │                   │
       │                   ▼
       │             LightGBM champion
       │                   │
       └─────────┬─────────┘
                 ▼
         daily forecast rows
                 │
                 ▼
      fail-closed validation
                 │
       evidence receipt + IDs
                 │
                 ▼
     frontend JSON generation
                 │
        ┌────────┴────────┐
        ▼                 ▼
 public/weeks/*     public/symbols/*
 public/screener     public/evidence/*
        │                 │
        └────────┬────────┘
                 ▼
            Next.js UI


                           FRESH QUOTE PATH

Finnhub WebSocket / REST · Alpaca IEX · Polygon fallback
                 │
                 ▼
       one lease-elected writer
                 │
                 ▼
            Upstash Redis
                 │
                 ▼
     /api/stocks/batch-price
                 │
                 ▼
          browser quote overlay
```

## 1. Market-implied expected move

A headline market expected move starts with an option chain, not a frontend formula.

### Step 1 — provider rows land in Parquet

The scheduled data pipeline synchronizes options data into partitioned Parquet. Raw provider shape is preserved at the storage boundary so normalization decisions can be audited later.

### Step 2 — DuckDB defines quote eligibility

`scripts/setup_duckdb_from_parquet.py` creates the analytical contract.

Relevant views are:

```text
v_options_raw
    ↓
v_options
    ↓
v_option_quote_quarantine
    ↓
v_straddle_candidates
    ↓
v_straddle_quote_quarantine
    ↓
v_atm_options
    ↓
v_straddle_features
```

The key design decision is that unusable quotes are **not silently cleaned into valid observations**. Crossed markets, nonpositive quotes, excessive spreads, stale/invalid rows, and unusable call/put pairs remain available in quarantine evidence but are excluded from decision-eligible ATM/straddle selection.

The same paired-options definitions feed research features and product publication so training cannot silently use one quote-quality rule while the UI uses another.

### Step 3 — reconciliation can stop publication

`scripts/build_data_reconciliation.py` summarizes source coverage, duplicate keys, quote rejection, upcoming-event chain coverage, ticker lifecycle, corporate actions, quarantine, and replay controls.

A critical exception sets `decision_safe=false` and stops scoring/publication. Advisory gaps can publish as `degraded`, but the exception remains visible.

### Step 4 — expected-move fields are built

The selected decision-eligible option pair produces research fields including:

```text
atm_iv
straddle_mid
em_straddle
em_straddle_pct
em_iv
em_iv_pct
dte
skew_atm
term_slope
```

These fields flow through the forecast/frontend builders rather than being recomputed ad hoc in React.

### Step 5 — frontend artifacts are generated

`tools/build_frontend_data.py` and `tools/frontend_data/` produce the browser-facing artifacts, primarily:

```text
apps/frontend/public/screener.json
apps/frontend/public/weeks/*.json
apps/frontend/public/symbols/<TICKER>.json
```

The symbol page reads the generated payload server-side and hydrates the client with that exact static research snapshot.

## 2. ML expected move

The ML estimate is intentionally a correction/research layer on top of the same point-in-time event data, not an independent live prediction feed.

### Step 1 — feature engineering

`apps/ml/feature_engineering.py` builds horizon-specific training tables for:

```text
T-1 · T-2 · T-3 · T-7 · T-14 · T-21
```

Features combine option-market state, historical earnings reactions, realized-volatility measures, volatility-history features, VIX/macro context, price/volume context, and event timing.

Training rows retain symbol/date metadata for chronology, slicing, and controls, while those metadata columns are not passed as ordinary model inputs.

### Step 2 — model training

`apps/ml/model_trainer.py` trains one point estimator plus P10/P25/P50/P75/P90 quantile heads for each supported horizon.

The production artifact is native LightGBM rather than an executable Python pickle.

### Step 3 — challenger validation

A retrain is not a promotion.

The control path validates:

```text
feature / target integrity
        ↓
chronological holdout
        ↓
point + quantile + interval gates
        ↓
4 expanding walk-forward windows
60-day validation / 5-day purge
        ↓
straddle-baseline comparison
        ↓
common-holdout champion comparison
        ↓
upcoming-event shadow scoring
        ↓
promotion decision
```

A challenger can complete training and still be rejected. This is expected behavior.

### Step 4 — signed immutable bundle

The model-control plane publishes immutable bundle contents before the signed champion pointer.

A bundle includes exact model files, ordered feature schema, source revision, digests, and validation identity. Railway verifies the pointer/manifest/digests and native-loads the complete bundle before atomic activation.

### Step 5 — daily scoring and forecast receipt

`scripts/daily_score.py` scores current upcoming-event feature rows with the active bundle.

Forecast validation checks feature/schema integrity, quantile behavior, arithmetic handoff, duplicates, and other publication invariants. Successful validation emits a content-addressed evidence receipt under the forecast evidence path.

A failed validation exits before downstream publication.

### Step 6 — frontend publication

Validated forecast fields such as:

```text
em_ml_pct
em_ml_abs
p10
p25
p50
p75
p90
model_horizon
ml_snapshot_date
```

are projected into the same static week/screener/symbol payloads used for the market-implied fields.

The browser therefore receives a research snapshot whose model and market context were validated together.

## 3. Spot-updated ML mode

The symbol page can optionally ask Railway to re-score a stored feature vector using a newer stock price.

That path is:

```text
browser
  ↓
Next.js /api/ml/predict
  ↓ HMAC
Railway FastAPI
  ↓
Neon stored feature_vector
  ↓
replace spot-derived price fields only
  ↓
active native LightGBM champion
  ↓
response
```

The response remains:

```json
{
  "decision_scope": "end_of_day_research",
  "market_data_mode": "end_of_day",
  "live_trading_eligible": false,
  "updated_inputs": ["spot"]
}
```

Options quotes, IV, Greeks, skew, term structure, earnings history, and other research inputs stay frozen at the validated snapshot. The UI must not relabel this as live options inference.

## 4. Stock quote shown in the hero

The stock quote uses a different path and can be fresher than the research snapshot.

During the regular quote window, one writer owns `quote:regular:lease` and writes `quote:<symbol>` values to Upstash.

The preferred writer uses Finnhub; Vercel can acquire the same lease as failover. Off-hours broad refresh uses Polygon without competing for regular-hours ownership. Selected extended-hours bars/quotes can use Alpaca IEX.

The browser reads through:

```text
/api/stocks/batch-price
```

That route returns quote source/session/freshness metadata and records demand so the writer can prioritize active symbols.

The symbol page deliberately distinguishes labels such as:

```text
Live · exchange
Live extended hours · IEX
Last close · exchange
Last quote · IEX
Snapshot · date
```

The headline daily change is anchored to the authoritative previous close rather than an after-hours IEX bar, preventing earnings reactions from being understated.

## 5. Evidence shown to the user

Current research evidence is projected into browser-safe artifacts such as:

```text
apps/frontend/public/evidence/forecast.json
apps/frontend/public/control-plane.json
```

The forecast receipt identifies the validated artifact bundle, coverage, controls, observation/scoring windows, and validation status.

The control-plane snapshot summarizes current data/model/release state and advisory exceptions.

These are intentionally projections rather than full operational manifests: the product can answer “what evidence supports this?” without publishing secrets, filesystem paths, or admin controls.

## 6. What is authoritative?

| Question | Authoritative source |
|---|---|
| Raw historical data | immutable/reconciled CSV + Parquet release |
| Analytical option eligibility | DuckDB views + reconciliation controls |
| ML feature order | active model-bundle metadata/schema |
| Active production model | signed champion control decision / activated bundle |
| Published forecast | validated forecast artifact + evidence receipt |
| Browser static research state | generated `public/` JSON from that validated run |
| Current stock quote | quote cache response + source/session metadata |
| Operational health | control-plane / service health surfaces |

## 7. Failure behavior

Quantiv is designed to degrade explicitly rather than manufacture freshness.

Examples:

- bad option rows → quarantined rather than selected;
- critical reconciliation exception → scoring/publication blocked;
- rejected challenger → previous champion remains active;
- failed/interrupted model download → previous verified bundle remains active;
- Railway prediction unavailable → browser can retain validated nightly ML fields;
- quote provider gap → last-confirmed/fallback/unavailable state rather than fabricated tick;
- advisory coverage/drift warning → publication may remain eligible but state is `degraded` and visible.

## 8. Where to read next

For deeper contracts:

- `docs/ARCHITECTURE.md` — production topology and workflows
- `docs/DUCKDB_ARCHITECTURE.md` — offline analytical boundary
- `docs/RECONCILIATION_CONTROL_PLANE.md` — data-quality and publication controls
- `docs/MODEL_CONTROL_PLANE.md` — model bundle, promotion, monitoring, rollback
- `docs/EVIDENCE_RECEIPTS.md` — content-addressed validation evidence
- `docs/DECISION_SCOPE.md` — end-of-day vs live-trading boundary
- `docs/HMAC_PROXY.md` — signed spot-update path

If an engineer understands this document and those six contracts, they should be able to orient themselves in Quantiv without reverse-engineering the repository from page components.
