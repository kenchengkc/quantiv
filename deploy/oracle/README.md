# Self-hosted deploy + infra cost study

A turnkey, **$0/mo** alternative to the managed hosting for Quantiv's backend —
plus the cost analysis and parity testing behind the build-vs-buy decision.

This directory is both a working migration and a small case study in measuring
real infrastructure cost and de-risking a host move for a live product.

## The system being hosted

Quantiv's backend is two long-running processes that share one Docker image
([`apps/backend/Dockerfile`](../../apps/backend/Dockerfile)):

- **api** — FastAPI service: options expected-move + LightGBM ML forecasts.
  Stateless request/response over Neon Postgres + Upstash Redis + R2.
- **worker** — holds a Finnhub WebSocket + REST poller during market hours and
  streams quotes into Redis. Needs to be **always-on**, which is what drives the
  hosting cost (it can't run serverless / scale-to-zero).

The frontend (Vercel) reaches the api at `https://api.usequantiv.com` behind an
HMAC shared secret. The data stores are all external and managed.

## Cost analysis (measured, not guessed)

Instrumented the actual bills rather than estimating:

| Component | How measured | Cost |
|---|---|---|
| Managed compute (2 services) | platform billing API — GB‑min × rate | **~$4/mo** usage (under a $5 plan floor) |
| Upstash Redis | command-mix from the analytics dashboard | **~$3/mo** (65k cmds/weekday @ $0.20/100k) |
| Vercel / Cloudflare / Neon / R2 | plan + free-tier limits | **$0** |
| **Total** | | **~$8/mo** |

The largest historical line item was Redis. The quote worker's **batched-write
path** (coalesce ticks, flush once per interval under a Redis Lua/lease guard)
cuts command volume ~6× — **438k → ~65k commands/weekday**, taking Redis from
~$27/mo to ~$3/mo. The biggest cost lever had already been pulled in code; the
analysis confirmed it was live and quantified the rest.

## The $0 migration design

Replaces the managed platform with a single always-free VM, keeping behavior
identical:

- **One image, two services + Caddy** for auto-renewing TLS
  ([`docker-compose.yml`](docker-compose.yml), [`Caddyfile`](Caddyfile)).
- **Stateless host.** The only local state (`/data`, ~58 MB) is a model +
  forecasts-parquet cache, reconstructed from R2 on boot
  ([`pull-data.sh`](pull-data.sh)). Postgres, Redis, and R2 stay external and
  untouched, so the move is a host swap, not a data migration.
- **Zero-downtime cutover.** The worker arbitrates a single writer via a Redis
  lease, so the old and new workers can run simultaneously during the DNS flip —
  no double-writes, and rollback is one DNS record.
- **Build hygiene.** A repo-root [`.dockerignore`](../../.dockerignore) keeps an
  11 GB `data/` tree, `.venv`, and `node_modules` out of the build context.

## Validation

Before recommending the move, the VM stack was built and tested against the live
backing services and compared to production request-by-request:

- Every endpoint the frontend actually calls (`/api/ml/{predict,status,batch-predict}`)
  returned **byte-identical results on the VM vs. live prod**, including the same
  HMAC enforcement and the same business-logic 404s.
- The worker was exercised end-to-end against a throwaway Redis — lease
  acquisition, Finnhub WebSocket subscribe, REST poll, and batch-flush writes —
  without touching production.
- A pre-existing prod bug (two legacy DuckDB endpoints 500 due to a parquet
  schema drift) was caught by the parity test and confirmed *not* a regression.

## Decision: build-vs-buy

The $0 path is fully built and tested, but the production deployment stays on
**managed hosting (~$5/mo)**. The deciding factor isn't functionality — it's that
a single free-tier VM has no auto-failover, while the managed platform self-heals
host failures. For a low-traffic product, paying ~$5/mo to avoid silent downtime
and ops overhead is the right trade. The migration here is the documented,
ready-to-run fallback if that calculus changes.

> Takeaway: the interesting work was measuring the real numbers, shipping the
> structural win (batched writes), and making a deliberate reliability/cost call —
> not chasing $0 for its own sake.

## Running it

See [`RUNBOOK.md`](RUNBOOK.md) for VM provisioning, firewall, DNS cutover, and
rollback. Quick path on a prepared host:

```bash
cp .env.example .env   # fill in (or generate from your platform's env)
./bootstrap.sh         # pull /data from R2, build, start api + worker + caddy
```
