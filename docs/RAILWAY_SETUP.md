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
- The Upstash Redis **TCP** URL (not the REST URL the frontend uses).
  Upstash → Details → "TLS-Endpoint" — `rediss://default:<password>@<host>:6379`.
- Optional: `ADMIN_API_KEY` (`openssl rand -hex 16`) for cache-bust/admin routes.
  Add the same value as a GitHub Actions secret if you want the weekly model
  retrain workflow to hot-sync fresh R2 models onto Railway automatically.

Set `BACKEND_SHARED_SECRET` to match Vercel (see [HMAC_PROXY.md](HMAC_PROXY.md)).
When set, direct public calls to `/api/ml/predict` return **401** without a
signature — only the Vercel proxy can call those routes.

---

## 1. Create the Railway service

1. Railway dashboard → **New Project** → **Deploy from GitHub repo** → pick
   this repo.
2. Railway may auto-detect **`@quantiv/frontend`** from the npm workspace in
   [`package.json`](../package.json). That service is for Vercel — **delete it**
   or ignore it. The backend is a separate Python/Docker service.
3. Add a service for the backend:
   - **Empty Service** → connect this repo, **or**
   - Set **Builder**: Dockerfile, **Dockerfile path**: `apps/backend/Dockerfile`,
     **Root directory**: `/` (repo root — required for `COPY apps/ml`).
4. [`railway.toml`](../railway.toml) must **not** set `startCommand` — Railway
   does not expand `${PORT:-8000}` in `startCommand`; use the Dockerfile `CMD`
   (`sh -c` + uvicorn) instead.

---

## 2. Attach a persistent volume

Railway volumes are **not** under Service → Settings. On the **project canvas**:

1. **⌘K** / **Ctrl+K** → **Create Volume**, or right-click the canvas → **Create Volume**.
2. Attach to the **backend** service.
3. **Mount path**: `/data`.

Or CLI: `railway volume add --mount-path /data`

---

## 3. Set environment variables

Service → **Variables**:

| Variable | Value | Notes |
|---|---|---|
| `DATA_BACKEND` | `hybrid` | Postgres + DuckDB |
| `DATA_DIR` | `/data` | Not `./data` |
| `DUCKDB_PATH` | `/data/quantiv.duckdb` | On the volume |
| `DATABASE_URL` | `postgres://...` | Same Neon URL as Vercel |
| `REDIS_URL` | `rediss://default:...@...upstash.io:6379` | TCP, not REST |
| `R2_ACCOUNT_ID` | Cloudflare account ID | |
| `R2_ACCESS_KEY_ID` | R2 token | secret |
| `R2_SECRET_ACCESS_KEY` | R2 token | secret |
| `R2_BUCKET` | `quantiv` | |
| `FRONTEND_URL` | `https://usequantiv.com` | CORS |
| `ADMIN_API_KEY` | `openssl rand -hex 16` | optional |
| `BACKEND_SHARED_SECRET` | `openssl rand -hex 32` | must match Vercel |
| `ENVIRONMENT` | `production` | |
| `POLYGON_API_KEY` | … | optional live context |

Do **not** put `NEXT_PUBLIC_*`, `POSTGRES_HOST=localhost`, or `ENABLE_ALPACA_*` here — those belong on Vercel.

---

## 4. Public networking & domains

1. **Networking** → enable public HTTP.
2. **Generate Domain** → target port **8000** (or match the `PORT` variable Railway injects; see logs for `Uvicorn running on ...:XXXX`).
3. Test: `curl https://YOUR-SERVICE.up.railway.app/health`
4. **Custom domain** `api.usequantiv.com` → add CNAME + TXT in **Vercel DNS**
   (domain uses `ns*.vercel-dns.com`). See Railway’s DNS panel for values.

---

## 5. Verify

```bash
curl https://api.usequantiv.com/health
# {"status":"healthy"|"degraded", ...}
```

`degraded` (e.g. redis or missing forecasts) is still your app responding — not Railway’s 502/404 JSON.

Check logs for `Uvicorn running on` and `Services initialized`.

---

## 6. Wire Vercel (HMAC proxy)

| Variable | Where | Value |
|---|---|---|
| `BACKEND_URL` | Vercel | `https://api.usequantiv.com` |
| `BACKEND_SHARED_SECRET` | Vercel **and** Railway | Same value (`openssl rand -hex 32`) |

Browser entrypoint: `POST https://usequantiv.com/api/ml/predict` (not Railway directly).
Details: [HMAC_PROXY.md](HMAC_PROXY.md).

For weekly model hot-sync, set these GitHub Actions secrets:

| Secret | Value |
|---|---|
| `ADMIN_API_KEY` | Same as Railway `ADMIN_API_KEY` |
| `RAILWAY_BACKEND_URL` | Optional; defaults to `https://api.usequantiv.com` |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `404 Application not found` | No healthy deploy; wrong service; domain on frontend service |
| `502 Application failed to respond` | Port mismatch — set Networking target port = `PORT` from logs; check crash logs |
| Healthcheck failed / uvicorn crash | Remove `startCommand` from `railway.toml`; redeploy |
| `hybrid` startup error | Set `DATABASE_URL`; use `/data` not `./data` |
| Empty forecasts | Expected on fresh volume — pull R2 or run `daily_score`; startup no longer hard-fails |

---

## Sanity checklist

- [ ] `curl https://api.usequantiv.com/health` returns app JSON (not Railway error envelope).
- [ ] Volume mounted at `/data`; env paths use `/data/...`.
- [ ] `DATABASE_URL` + `REDIS_URL` set.
- [ ] Vercel DNS: CNAME `api` → Railway target.
