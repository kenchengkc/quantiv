# HMAC-signed Vercel ↔ Railway proxy

The browser never talks to Railway directly. **Vercel** (`apps/frontend`) signs
server-side requests; **Railway** (`apps/backend`) verifies them. Public health
checks stay open.

## Files

| Piece | Path |
|-------|------|
| Vercel client | [`apps/frontend/lib/backendProxy.ts`](../apps/frontend/lib/backendProxy.ts) |
| Next.js routes | [`apps/frontend/app/api/ml/predict/route.ts`](../apps/frontend/app/api/ml/predict/route.ts), `/coverage`, `/batch-predict` |
| FastAPI middleware | [`apps/backend/middleware/hmac_auth.py`](../apps/backend/middleware/hmac_auth.py) |

## Signature contract

Both sides compute:

```text
canonical = "{METHOD}\n{PATH}\n{TIMESTAMP_MS}\n{SHA256_HEX(body)}"
signature = HMAC-SHA256(BACKEND_SHARED_SECRET, canonical)   # lowercase hex
```

Headers on every proxied request:

| Header | Value |
|--------|--------|
| `X-Quantiv-Timestamp` | Milliseconds since epoch (string) |
| `X-Quantiv-Signature` | Hex digest above |

Rules:

- `PATH` is the URL path only (e.g. `/api/ml/predict`), no host or query string.
- `BODY` is the raw request bytes (for JSON, use the exact string you send).
- Timestamps more than **30 seconds** from server time are rejected (replay window).
- If `BACKEND_SHARED_SECRET` is **unset** on Railway, middleware is a no-op (local `python main.py` without the secret).

## Exempt paths (no HMAC)

- `/health`, `/docs`, `/redoc`, `/openapi.json`
- `/api/admin/*` (uses `X-API-Key` + `ADMIN_API_KEY` instead)

## Environment variables

| Where | Variable | Example |
|-------|----------|---------|
| **Vercel** | `BACKEND_URL` | `https://api.usequantiv.com` |
| **Vercel** | `BACKEND_SHARED_SECRET` | Same value as Railway (`openssl rand -hex 32`) |
| **Railway** | `BACKEND_SHARED_SECRET` | Must match Vercel exactly |
| **Railway** | `FRONTEND_URL` | `https://usequantiv.com` (CORS only) |

Generate once:

```bash
openssl rand -hex 32
```

Do **not** expose `BACKEND_SHARED_SECRET` to the browser or `NEXT_PUBLIC_*`.

## User-facing flow (`/api/ml/*`)

```text
Browser  →  POST /api/ml/...  →  Vercel (signs + forwards)
                                      ↓
                               Railway /api/ml/...
```

`/api/ml/predict` route behaviour:

1. If `BACKEND_URL` or secret missing → **nightly fallback** from `public/symbols/{SYM}.json`.
2. Railway **4xx** → returned to client (e.g. no feature snapshot).
3. Railway **5xx / timeout** → nightly fallback with `source: "nightly_fallback"`.

`/api/ml/batch-predict` falls back per item when the backend is unavailable.
`/api/ml/coverage` returns a 503 when the backend proxy is not configured.

Current proxied endpoints:

| Browser path | Railway path | Purpose |
|--------------|--------------|---------|
| `POST /api/ml/predict` | `POST /api/ml/predict` | Single-symbol live re-score |
| `POST /api/ml/batch-predict` | `POST /api/ml/batch-predict` | Per-item batch re-score; partial failures are returned per item |
| `POST /api/ml/coverage` | `POST /api/ml/coverage` | Feature-vector coverage totals and symbol/event horizon availability |

## Local development

| Setup | Behaviour |
|-------|-----------|
| No `BACKEND_*` on Vercel env | `/api/ml/predict` uses nightly JSON only |
| Railway without `BACKEND_SHARED_SECRET` | Direct `curl` to `localhost:8000` works |
| Both secrets set locally | Point `BACKEND_URL=http://localhost:8000`, call `/api/ml/predict` on Next |

## Production checks

```bash
# Health — no HMAC (should always work)
curl https://api.usequantiv.com/health

# Direct Railway — should 401 once secret is set on Railway
curl -X POST https://api.usequantiv.com/api/ml/predict \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"CRM","horizon_days":7,"earnings_date":"2026-05-27"}'

# Through Vercel proxy (correct path for browsers)
curl -X POST https://usequantiv.com/api/ml/predict \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"CRM","horizon_days":7,"spot_override":181.19,"earnings_date":"2026-05-27"}'
```

## Adding a new proxied route

1. Add the FastAPI handler on Railway.
2. Add `apps/frontend/app/api/.../route.ts` that calls `proxyJsonPost('/your/path', body)`.
3. Keep `PATH` in the signature identical to the backend route string.
4. Extend `proxyJsonGet` in `backendProxy.ts` if you need GET (not implemented yet).

## Tests

```bash
pytest apps/backend/tests/test_hmac_auth.py -q
```

Contract must match `backendProxy.ts` — if you change one, change both.
