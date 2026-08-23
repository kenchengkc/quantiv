# Quantiv system architecture

This guide documents Quantiv's production data flow, application services, live quote system, and optional ML backend. For installation and common contributor commands, start with the root [README](../README.md).

## Overview

Quantiv has three independent paths:

1. **Static dashboard generation** — scheduled ingestion, validation, scoring, and JSON publication.
2. **Live ML inference** — an optional signed Vercel-to-Railway request path.
3. **Live quotes** — multiple writers sharing an Upstash Redis cache.

```mermaid
flowchart TB
  subgraph nightly["Nightly data and ML pipeline — GitHub Actions"]
    PROVIDERS["DoltHub · Finnhub · FMP · FRED"]
    SYNC["Sync and reconcile data"]
    FILES["CSV and Parquet artifacts"]
    GATE["Integrity and freshness checks"]
    DUCK[(DuckDB)]
    SCORE["LightGBM scoring"]
    BUILD["Build frontend JSON"]
    PUB["Static JSON in apps/frontend/public"]
    SEC["SEC EDGAR"]

    PROVIDERS --> SYNC --> FILES --> GATE --> DUCK
    DUCK --> SCORE --> BUILD --> PUB
    DUCK --> BUILD
    SEC -->|Ticker metadata| BUILD
  end

  R2[(Cloudflare R2)]
  FILES <-->|Artifact sync| R2

  subgraph application["Application serving"]
    VERCEL["Next.js on Vercel"]
    RAILWAY["Railway FastAPI — optional"]
    NEON[(Neon Postgres)]

    PUB --> VERCEL
    VERCEL <-->|Watchlists| NEON
    VERCEL -->|HMAC live prediction| RAILWAY
    R2 -->|Parquet and models| RAILWAY
    SCORE -->|Recent forecasts| NEON
  end

  subgraph quotes["Live quote pipeline"]
    WRITERS["Railway worker · Vercel cron · Polygon refresh"]
    UPSTASH[(Upstash Redis)]
    BATCH["Vercel batch-price API"]
    INTEREST["quote:interest rankings"]

    WRITERS -->|Write quote keys| UPSTASH
    UPSTASH -->|Read quotes| BATCH
    BATCH -->|Return prices| VERCEL
    BATCH -->|Record demand| INTEREST
    INTEREST -->|Prioritize symbols| WRITERS
  end
```

## Static dashboard path

The calendar, screener, and baseline symbol pages are served from generated JSON rather than a FastAPI request on every navigation.

```text
Provider data
→ CSV and Parquet artifacts
→ integrity checks
→ DuckDB views
→ ML scoring
→ frontend JSON
→ Next.js on Vercel
```

Generated assets live under `apps/frontend/public/`, primarily in:

```text
apps/frontend/public/
├── weeks/
├── symbols/
├── screener.json
├── ticker-names.json
└── ticker-exchanges.json
```

Provider outputs are reconciled into `data/earnings_calendar.csv`. The integrity gate must pass before downstream generation proceeds.

## Live ML path

When configured, symbol and watchlist pages can request live re-inference through a signed proxy:

```text
Vercel
→ HMAC-signed request
→ Railway FastAPI
→ Postgres and DuckDB-backed features
→ prediction response
```

The proxy uses `BACKEND_URL` and `BACKEND_SHARED_SECRET`. See [HMAC_PROXY.md](HMAC_PROXY.md) for signing details.

If Railway is unavailable, the frontend can continue displaying ML fields embedded in nightly static JSON.

### Prediction implementations

The current frontend path uses:

```text
POST /api/ml/predict
→ predict_service
→ Postgres feature_vector
```

Older DuckDB-backed GET routes remain available for direct backend use:

```text
GET /em/*
GET /api/expected-move
→ MLService
→ DuckDB
```

## Live quote path

All quote writers share Upstash keys in the form `quote:{symbol}`.

### Writers

1. **Railway quote worker** — primary scaled writer using Finnhub WebSocket data for high-priority symbols and REST for the long tail.
2. **Vercel `refresh-prices`** — regular-hours fallback using a rotating Finnhub cursor and selected Alpaca IEX extended-hours data.
3. **Vercel `refresh-broad`** — off-hours broad refresh using Polygon grouped-daily data.

Client reads go through `/api/stocks/batch-price`. That route reads cached quotes, applies a short in-memory cache, and records context-weighted demand in the `quote:interest` sorted set. The Railway worker reads those rankings to prioritize active symbols.

Protected cron routes use `CRON_SECRET` and `BROAD_REFRESH_SECRET`.

## Services

| Service | Role | Required? |
|---|---|---|
| Vercel | Next.js app, API routes, and static JSON | Yes for hosted web deployment |
| Cloudflare R2 | Parquet datasets and model artifacts | Yes for the hosted data pipeline |
| GitHub Actions | Validation, refresh, enrichment, and model workflows | Yes for automated refreshes |
| Upstash Redis | Quote cache and interest rankings | Required for live quotes |
| Finnhub | Earnings overlay and regular-hours quote source | Required for the primary quote workflow |
| Clerk | Watchlist and admin authentication | Optional |
| Neon | Watchlists, forecasts, and cron metadata | Optional for public browsing |
| Railway FastAPI | Live ML inference and legacy backend routes | Optional |
| Railway quote worker | Scaled quote ingestion | Optional |
| Polygon | Off-hours broad quote refresh | Optional |
| Alpaca IEX | Intraday bars and selected extended-hours data | Optional |
| Cloudflare Worker | High-frequency Vercel quote trigger | Optional |

Production DNS:

```text
usequantiv.com      → Vercel
api.usequantiv.com  → Railway
```

See [RAILWAY_SETUP.md](RAILWAY_SETUP.md) for deployment instructions.

## Data providers

| Source | Usage |
|---|---|
| DoltHub | Historical earnings-calendar baseline |
| Finnhub | Near-term earnings, profiles, logos, and live quotes |
| Financial Modeling Prep | Earnings, EPS, and revenue enrichment |
| FRED | VIX and macro-volatility inputs |
| SEC EDGAR | Ticker names, exchanges, and company identity metadata |
| Alpaca IEX | Intraday and selected extended-hours prices |
| Polygon | Off-hours broad quote refresh |

## Frontend API routes

| Route | Purpose |
|---|---|
| `/api/stocks/batch-price` | Read cached quotes and record quote demand |
| `/api/stocks/intraday` | Return Alpaca IEX intraday bars |
| `/api/stocks/search` | Search supported stock and ETF metadata |
| `/api/watchlist/*` | Watchlist CRUD and reordering |
| `/api/ml/predict` | Proxy one live prediction to Railway |
| `/api/ml/batch-predict` | Proxy multiple live predictions |
| `/api/ml/status` | Return protected ML diagnostics |
| `/api/cron/refresh-prices` | Refresh regular-hours and selected extended-hours quotes |
| `/api/cron/refresh-broad` | Refresh the broad universe outside market hours |

## Automation

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | Pull requests and pushes to `main` | Lint, build, pytest, and Playwright |
| `daily-refresh.yml` | Nightly | Data refresh, scoring, frontend generation, and optional training |
| `refresh-broad.yml` | Weekday off-hours | Polygon quote-cache warming |
| `refresh-ticker-names.yml` | Quarterly | SEC ticker-name and exchange refresh |
| Enrichment workflows | Scheduled or manual | Provider-specific metadata enrichment |

The nightly workflow broadly performs:

```text
Pull R2 artifacts
→ synchronize and reconcile providers
→ validate the earnings calendar
→ push Parquet artifacts
→ build DuckDB views
→ score recent observations
→ validate forecasts and publish a content-addressed evidence receipt
→ import forecasts
→ update market-cap and popularity metadata
→ generate frontend JSON
→ commit generated artifacts
→ redeploy Vercel
```

Sunday runs may retrain a signed LightGBM challenger. Mandatory walk-forward,
baseline, calibration, forecast-handoff, common-holdout, and shadow gates decide
whether its immutable bundle replaces the champion. Railway atomically activates
only the signed champion; realized monitoring can sign a rollback to the previous
bundle. See [Model control plane](MODEL_CONTROL_PLANE.md). Saturday runs perform
a Finnhub profile and logo sweep.

ML/model publication is fail-closed and produces a shared run-level evidence
receipt rather than per-value lineage records. See
[Evidence receipts](EVIDENCE_RECEIPTS.md) for the contract and zero-database-cost
publication path.

The data pipeline also emits one exception-first reconciliation manifest from
the existing DuckDB views and mapping files. Critical exceptions block scoring;
warnings expose coverage or instrumentation gaps. See
[Reconciliation control plane](RECONCILIATION_CONTROL_PLANE.md).

## Data behavior and limitations

- Headline expected move is generally straddle- or IV-derived unless an ML result is available.
- ML values may come from nightly static JSON or a successful live Railway prediction.
- Live quotes are cached and may be stale or unavailable when providers or refresh workers are not configured.
- Provider datasets can disagree on announcement dates, timing, EPS, and revenue estimates.
- The integrity gate reduces inconsistencies but cannot guarantee provider accuracy.
- `/public/weekly.json` is a legacy fallback; primary week data lives under `/public/weeks/`.
- The `Popular` filter uses weights from `tools/build_popular_weights.py`, with its threshold configured in `apps/frontend/lib/popular.ts`.
