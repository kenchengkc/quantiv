# Railway setup — Quantiv FastAPI backend

Step-by-step to wire the `apps/backend` FastAPI service to Railway. Do these
in order. The whole thing takes ~30 minutes if you have the credentials
ready.

Before you start, have these handy:
- A Railway account (Hobby plan is fine — $5/mo).
- The Neon `DATABASE_URL` (same one Vercel uses for the watchlist).
- Cloudflare R2 account ID + an API token with read access to the `quantiv`
  bucket. **The secret access key cannot be recovered — if you've lost it,
  generate a new token at Cloudflare → R2 → Manage API tokens.**
- The Upstash Redis TCP URL (different from the REST URL the frontend uses).
  Get it from Upstash → Details → "TLS-Endpoint" — looks like
  `rediss://default:<password>@<host>:6379`.
- A generated HMAC key: `openssl rand -hex 32` — save it; you'll paste it
  into both Railway and Vercel.

---

## 1. Create the Railway service

1. Railway dashboard → **New Project** → **Deploy from GitHub repo** → pick
   this repo.
2. Railway detects [`railway.toml`](../railway.toml) automatically and
   builds [`apps/backend/Dockerfile`](../apps/backend/Dockerfile) from the
   repo root. No additional build config is needed.
3. After the first deploy lands (it will fail the healthcheck — that's
   expected until env + volume are set), open the service settings.

## 2. Attach a persistent volume

1. Service → **Settings** → **Volumes** → **+ New Volume**.
2. Mount path: `/data`.
3. Size: 5 GB is plenty for the LightGBM models + the rolling Parquet
   window. The lazy R2 fetcher only pulls files when first requested.

## 3. Set environment variables

Service → **Variables** → add these. Anything marked `secret` should be
generated/copied; never commit them.

| Variable | Value | Notes |
|---|---|---|
| `DATA_BACKEND` | `hybrid` | Postgres + DuckDB, per Path B. |
| `DUCKDB_PATH` | `/data/quantiv.duckdb` | Lives on the mounted volume. |
| `DATA_DIR` | `/data` | Root for `/data/forecasts/*.parquet` + `/data/models/*.joblib`. |
| `DATABASE_URL` | `postgres://...` | Same Neon URL the watchlist uses. |
| `REDIS_URL` | `rediss://default:...@...upstash.io:6379` | TCP, not REST. |
| `BACKEND_SHARED_SECRET` | `<openssl rand -hex 32>` | secret · same value in Vercel. |
| `R2_ACCOUNT_ID` | `<Cloudflare R2 account ID>` | |
| `R2_ACCESS_KEY_ID` | `<R2 token access key>` | secret |
| `R2_SECRET_ACCESS_KEY` | `<R2 token secret>` | secret · regenerate if lost. |
| `R2_BUCKET` | `quantiv` | Whatever bucket holds your parquet + models. |
| `FRONTEND_URL` | `https://usequantiv.com` | Adds to CORS allow-list. |
| `ADMIN_API_KEY` | `<openssl rand -hex 16>` | secret · for `/api/admin/*`. |
| `ENVIRONMENT` | `production` | Switches FastAPI off debug. |

Optional but recommended:
- `POLYGON_API_KEY` — enables live-context expansion on `/api/expected-move`.
  Leave unset to skip; the route still works without it.

## 4. Configure a custom domain (optional but recommended)

Service → **Settings** → **Networking** → **Generate Domain** gives you
something like `quantiv-backend.up.railway.app`. To use `api.usequantiv.com`
instead:

1. Click **Custom Domain** → enter `api.usequantiv.com`.
2. Railway shows a CNAME target — point your DNS at it.
3. Wait for the cert to issue (~1 min). The healthcheck on `/health`
   passes automatically once env + volume are ready.

## 5. Verify

After the next deploy lands:

```bash
curl https://api.usequantiv.com/health
# Should return: {"status":"healthy","timestamp":"...","services":{...}}
```

If `services.postgres` or `services.duckdb` reports `unhealthy`, the env
vars in step 3 are wrong. The Railway logs (`railway logs`) will show the
specific connection error.

## 6. Wire Vercel to call this service

In the Vercel project settings → **Environment Variables**, add:

| Variable | Value |
|---|---|
| `BACKEND_URL` | `https://api.usequantiv.com` |
| `BACKEND_SHARED_SECRET` | Same value as Railway. |

These two env vars are read by the Next.js proxy route added in Phase 3 of
the backend build. Without `BACKEND_URL`, the proxy falls back to
the nightly static JSON (the same graceful-degradation path the route uses
when Railway is cold).

## 7. Schedule the pre-warm cron

The Cloudflare Worker at `workers/refresh-prices` will get a small addition
in Phase 5 to ping `https://api.usequantiv.com/health` every 5 minutes
during market hours. No action needed on your side until then — the worker
already deploys via `wrangler publish`.

---

## Sanity checklist

After everything is wired:

- [ ] `curl https://api.usequantiv.com/health` returns 200.
- [ ] Railway logs show `Ensured DuckDB em_forecasts view` on startup
      (the lazy R2 fetch ran and resolved the latest forecasts).
- [ ] `psql $DATABASE_URL -c "SELECT COUNT(*) FROM em_forecasts"` shows
      ~4–5k rows (one nightly snapshot worth).
- [ ] `BACKEND_URL` env var set in Vercel.
- [ ] `BACKEND_SHARED_SECRET` matches between Railway and Vercel.

Once those four are green, ping me — I'll start Phase 1 (the
`/api/ml/predict` route + Upstash-backed caching + lazy model loading).
