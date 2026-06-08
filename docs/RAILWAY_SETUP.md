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
| `FINNHUB_API_KEY` | … | required for the quote worker service only |

Do **not** put `NEXT_PUBLIC_*`, `POSTGRES_HOST=localhost`, or `ENABLE_ALPACA_*` here — those belong on Vercel.

### Optional: Railway regular-hours quote worker

For fresher regular-hours stock prices, run a second Railway service from the
same repo/image and override the start command to:

```bash
python workers/quote_worker.py
```

Use the same Dockerfile (`apps/backend/Dockerfile`) and the same `REDIS_URL`,
`DATABASE_URL`, `FINNHUB_API_KEY`, `FRONTEND_URL`, and `ENVIRONMENT=production`
variables. The worker writes the same `quote:{SYMBOL}` Redis cache records that
the Vercel cron route writes, so the frontend read path stays unchanged.

Optional worker tuning:

| Variable | Default | Notes |
|---|---:|---|
| `QUOTE_WORKER_REST_PER_MIN` | `55` | Finnhub REST `/quote` calls per minute. Keep below the free-plan `60/min` ceiling. |
| `QUOTE_WORKER_WS_SYMBOLS` | `50` | Finnhub WebSocket subscriptions for top interest-ranked symbols. Free plan allows 50. |
| `QUOTE_WORKER_ENABLE_WEBSOCKET` | `1` | Set `0` to run REST-only. |
| `QUOTE_WORKER_UNIVERSE_REFRESH_S` | `300` | Rebuild watchlist/earnings/interest ranking interval. |
| `QUOTE_WORKER_WS_REFRESH_S` | `120` | Reconnect/resubscribe interval for the current top-50 set. |
| `QUOTE_WORKER_STATUS_REFRESH_S` | `60` | Heartbeat interval. The status key keeps a 180-second TTL. |
| `QUOTE_WORKER_CURSOR_CHECKPOINT_S` | `60` | Persist the in-memory REST cursor once per minute. |
| `QUOTE_WORKER_LEASE_TTL_S` | `90` | Single-writer lease TTL; renewed every third of this interval. |
| `QUOTE_WORKER_QUOTE_FLUSH_S` | `5` | Dirty-quote batch flush interval when batching is enabled. |
| `QUOTE_WORKER_BATCH_WRITES` | `0` | Set to `1` only after the lease heartbeat is healthy; batches dirty quote keys with `MSET`. |

Once the worker is healthy, set this on Vercel production:

| Variable | Value | Effect |
|---|---|---|
| `QUOTE_REFRESH_PROVIDER` | `railway` | Makes `/api/cron/refresh-prices?window=regular` return a no-op so Vercel stops doing regular-hours Finnhub polling. Premarket/after-hours Alpaca reporter refreshes still run. |

The worker writes a heartbeat to Redis key `quote:worker:status` with counts,
top universe size, lease protocol, batch metrics, and last REST/WebSocket
symbols. During the regular quote window it also owns
`quote:regular:lease`; outside that window it releases the lease and stops
heartbeats so weekends do not generate idle Redis traffic. Once the
lease-protocol marker is present, the Vercel route automatically acquires the
same lease and fails over only when Railway's heartbeat/ownership is stale.
Before that marker exists, the route retains the legacy manual-failback
behavior to avoid running two Finnhub producers during deployment.

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

For ML serving diagnostics, call the signed Vercel route:

```bash
curl -X POST https://usequantiv.com/api/ml/status \
  -H 'Content-Type: application/json' \
  -d '{"fresh_window_days":7}'
```

The response includes `latest_import` when Neon has received a forecast
import from `scripts/import_recent_to_postgres.py`. Use that to reconcile
workflow counts (`source_rows`, duplicate drops, upserted rows, horizon
counts) against the backend's live feature-vector totals. It also includes
`supported_horizons`, `missing_fresh_horizons`, `missing_model_horizons`, and
`coverage_gaps` so absent T1/T2/T3-style coverage is visible instead of
looking like a backend outage.

The same status payload is rendered at `https://usequantiv.com/ml-status`
for a browser-friendly operational view. Both the page and
`/api/ml/status` require Clerk sign-in plus an email listed in the Vercel
server env var `ML_STATUS_ADMIN_EMAILS` (comma-separated). `ADMIN_EMAILS`
is accepted as a fallback.

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
