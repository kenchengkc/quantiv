# Cloudflare Worker — refresh-prices cron

Replaces the throttled GitHub Actions cron with a real 5-minute scheduler.

```
Cloudflare Worker (cron */5)  →  Vercel /api/cron/refresh-prices
                                  ↓
                                  Upstash Redis (per-ticker quote cache)
                                  Finnhub API (rate-limited 60/min)
```

The Worker is a 30-line stub that just `fetch()`'s the Vercel route every
5 minutes with a Bearer token. All the actual refresh logic lives in
[apps/frontend/app/api/cron/refresh-prices/route.ts](../../apps/frontend/app/api/cron/refresh-prices/route.ts).

## One-time setup (~10 min)

### 1. Add `CRON_SECRET` to Vercel

```bash
# Generate a random secret
openssl rand -hex 32

# Add to Vercel
vercel env add CRON_SECRET production
# paste the value when prompted
vercel env add CRON_SECRET preview     # if you want preview to also accept it
```

Redeploy so the new env is live (or wait for the next push).

### 2. Verify the Vercel route works

```bash
curl -H "Authorization: Bearer <your-secret>" \
  https://usequantiv.com/api/cron/refresh-prices
```

Expected response: `{"universe":888,"batchSize":300,"cursor":0,"nextCursor":300,"fetched":300,"failed":0,"durationMs":325000}`

(Takes ~5 min — Vercel pages 300 symbols at 55/min.)

### 3. Install + deploy the Worker

```bash
cd workers/refresh-prices
npm install
npx wrangler login         # opens browser for Cloudflare auth (one-time)
npx wrangler secret put REFRESH_URL
# paste: https://usequantiv.com/api/cron/refresh-prices
npx wrangler secret put CRON_SECRET
# paste: same value you set in Vercel
npx wrangler deploy
```

Output will include the Worker URL — bookmark it.

### 4. Verify the cron is firing

```bash
npx wrangler tail
```

Wait for the next :00 / :05 / :10 etc. You'll see a log line per fire.
Within ~6 min you should see `refresh response: 200` followed shortly by
the symbol counts in Vercel's function logs (`vercel logs <deployment>`).

### 5. Disable the GitHub Actions cron

The GH workflow keeps firing (~hourly) until you stop it. Edit
`.github/workflows/refresh-prices.yml` and comment out the `schedule:`
section, leaving `workflow_dispatch:` so you can still trigger it manually.

```yaml
on:
  # schedule:
  #   - cron: '*/5 * * * *'
  workflow_dispatch:
```

Commit + push.

## Cost

- **Cloudflare Workers Free**: 100,000 req/day. Cron uses 288/day. ✓
- **Vercel Hobby**: 100GB-hr/mo Fluid Compute. 288 invocations × ~5 min × 1 vCPU = ~24 GB-hr/mo. ✓
- **Finnhub**: same 60/min rate limit as before; we still pace at 55/min. ✓
- **Total monthly cost: $0**

## Troubleshooting

- **Worker fires but Vercel returns 401** → secret mismatch. Re-set in both
  Vercel and the Worker, redeploy both.
- **Worker fires but Vercel times out at 300s** → expected; the Vercel route
  finishes the work in the background. Worker logs `non-ok response: 504` are
  cosmetic. The Redis cache still updates correctly.
- **Cron not firing** → check `wrangler tail`. If silent, the deploy didn't
  attach the cron trigger; rerun `wrangler deploy`.
- **Want to trigger manually for testing** → just hit the URL with curl.
