# Deployment Guide (Consolidated)

> **This is the single authoritative deployment guide.** Previous files
> (`DEPLOYMENT_GUIDE.md`, `DEPLOYMENT_CHECKLIST.md`, `DEPLOY_NOW.md`,
> `README_DEPLOYMENT.md`, `docs/DEPLOYMENT_GUIDE.md`,
> `docs/PRODUCTION_DEPLOYMENT.md`) are superseded by this document.

## Architecture

| Component | Platform | Notes |
|-----------|----------|-------|
| Frontend  | Vercel   | Next.js 14, automatic deploys from `main` |
| Backend   | Railway  | FastAPI, Nixpacks build |
| Postgres  | Railway / Supabase / Docker | Partitioned tables |
| Redis     | Upstash  | Serverless, REST + TCP |
| ML Pipeline | Docker batch job | On-demand via `docker compose --profile batch` |

## Prerequisites

1. **Vercel** account with the frontend project linked
2. **Railway** account with the backend project linked
3. **Upstash** Redis instance
4. **Postgres** instance (Railway add-on or external)
5. **Polygon.io** API key (free tier works for 5-10 users)

## Environment Variables

Copy `config/.env.example` and fill in values for your target environment.

### Required
| Variable | Where | Description |
|----------|-------|-------------|
| `DATABASE_URL` | Backend | Full Postgres connection string |
| `REDIS_URL` | Backend | Redis TCP/TLS URL |
| `POLYGON_API_KEY` | Backend + GitHub Actions | Market data API |
| `ADMIN_API_KEY` | Backend | Secret key for admin endpoints |
| `NEXT_PUBLIC_API_URL` | Frontend | Backend URL (e.g. `https://quantiv-api.railway.app`) |
| `NEXT_PUBLIC_APP_URL` | Frontend | Frontend URL (e.g. `https://quantiv.vercel.app`) |

### Optional
| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_BACKEND` | `postgres` | `postgres`, `duckdb`, or `hybrid` |
| `FRONTEND_URL` | — | Custom domain added to CORS |
| `FMP_API_KEY` | — | Financial Modeling Prep key |

## Deploy Steps

### 1. Frontend (Vercel)

```bash
cd apps/frontend
vercel --prod
```

Or push to `main` — Vercel auto-deploys if linked.

Set env vars in Vercel dashboard → Settings → Environment Variables.

### 2. Backend (Railway)

```bash
cd apps/backend
railway up
```

Set env vars in Railway dashboard → Variables.

The `railway.json` configures Nixpacks build + uvicorn start command.

### 3. Database

Run the schema migrations against your Postgres instance:

```bash
psql $DATABASE_URL -f infrastructure/database/create-postgres-schema.sql
psql $DATABASE_URL -f infrastructure/database/create-serving-schema.sql
psql $DATABASE_URL -f infrastructure/database/create-em-schema.sql
```

### 4. Seed Data

Run the ML pipeline to generate initial forecasts:

```bash
python scripts/setup_ml_pipeline.py --local
```

### 5. GitHub Actions

Set these secrets in GitHub → Settings → Secrets:
- `POLYGON_API_KEY`
- `VERCEL_DEPLOY_HOOK` (optional, for auto-redeploy after weekly data update)

## Health Check

```bash
# Backend
curl https://quantiv-api.railway.app/health

# Frontend
curl https://quantiv.vercel.app/api/health
```

## Local Development

```bash
# Start all services
docker compose -f infrastructure/docker/docker-compose.yml up

# Or individually
npm run dev:frontend   # Terminal 1
npm run dev:backend    # Terminal 2
```

Ensure `config/.env.local` has `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` set
(docker-compose references these via `${VAR}` interpolation).
