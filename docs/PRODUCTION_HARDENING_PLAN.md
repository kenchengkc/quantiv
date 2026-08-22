# Production hardening implementation plan

## Objective

Raise Quantiv's production confidence without changing its product architecture:

- protect the expensive public API surface;
- cover the highest-risk quote, ML, watchlist, and generated-data contracts;
- make CI exercise the same artifacts deployed to Vercel, Railway, and Cloudflare;
- establish safe operational controls and repeatable verification;
- improve dependency, Docker, and documentation hygiene without broad UI rewrites.

## Guiding constraints

- Preserve the static-first frontend and existing graceful ML fallback behavior.
- Reuse Upstash Redis and SlowAPI instead of adding a new infrastructure service.
- Keep rate limiting fail-open when its storage is unavailable.
- Keep every runtime protection configurable through documented environment variables.
- Prefer deterministic unit and contract tests; tests must not call paid market-data APIs.
- Avoid mass formatting, model retraining, database framework replacement, and large component refactors.

## Phase 1: Critical test coverage

### Frontend API routes

- Add route-level Vitest coverage for:
  - ML predict and batch-predict validation, proxy success, upstream errors, and nightly fallback;
  - watchlist authentication, CRUD, normalization, ordering, and revision behavior;
  - broad quote refresh authentication, market-window gating, and representative Redis writes.
- Add reusable request and dependency mocks where they reduce duplication.

### Quote scheduling

- Extract the Cloudflare Worker's market-hours guard into a testable module.
- Exercise exact premarket, regular, and after-hours boundaries.
- Exercise weekends, NYSE holidays, and dates on both sides of daylight-saving changes.
- Run the frontend and Worker implementations against shared contract vectors.

### Backend and generated data

- Add HTTP-level HMAC tests for valid, stale, missing, and tampered requests.
- Cover ML predict and batch routing through FastAPI middleware.
- Verify production docs exposure and rate-limit behavior.
- Validate committed screener and weekly JSON shape, event counts, identity uniqueness, and manifest links.

## Phase 2: Runtime hardening

### Frontend

- Add Upstash sliding-window limits to unauthenticated, expensive endpoints.
- Cache limiter instances instead of constructing a Redis client per request.
- Return `429` responses with `Retry-After` and rate-limit metadata.
- Support a `RATE_LIMIT_ENABLED` emergency switch and fail open on missing configuration or transient Redis errors.
- Add HSTS, anti-framing, MIME-sniffing, referrer, permissions, and DNS-prefetch headers.
- Defer Content Security Policy until Clerk and external image/font domains have been validated in report-only mode.

### Backend

- Activate SlowAPI middleware and its existing default limit.
- Use Redis-backed counters when `REDIS_URL` exists and an in-memory fallback locally.
- Exempt health and authenticated administrative operations where throttling would impair recovery.
- Disable OpenAPI, Swagger UI, and ReDoc by default in production; allow an explicit temporary override.
- Keep HMAC validation ahead of expensive route handling.
- Apply the same conservative ticker validation used at the frontend boundary.

## Phase 3: Delivery safeguards

- Expand CI to run:
  - frontend lint, type-check, production build, Vitest, and Playwright;
  - backend, ML, script, and tool Ruff/pytest suites;
  - Cloudflare Worker type-check and Vitest;
  - the production Railway Docker build and a container health smoke test.
- Validate committed public JSON artifacts on every relevant push.
- Run public production smoke checks after changes reach `main`, with retries for deployment propagation.
- Add CodeQL scanning and grouped Dependabot updates for npm and Python dependency surfaces.

## Phase 4: Configuration and repository cleanup

- Make Docker Compose use the same repository-root backend build context as Railway.
- Standardize local and CI Node.js images on Node 20.
- Align Python ABI-sensitive NumPy/Pandas constraints.
- Remove only dependencies proven unused by runtime imports.
- Expand Ruff coverage after fixing the existing finite baseline violations.
- Provide unified root commands for routine checks.

## Phase 5: Operations and maintenance documentation

- Add a production runbook covering:
  - service topology and ownership;
  - health and smoke checks;
  - stale quote and ML fallback diagnosis;
  - rate-limit rollout and emergency disable controls;
  - Vercel, Railway, Worker, and data rollback procedures.
- Add a security policy and contribution/check guide.
- Keep `.env.example`, HMAC documentation, and the documentation index synchronized with runtime behavior.

## Acceptance criteria

The hardening pass is complete when:

1. Existing and new frontend unit tests pass.
2. Frontend lint, TypeScript, and production build pass.
3. Public Playwright specifications pass; authenticated specifications run when Clerk test credentials are configured.
4. Backend, ML, script, and tool pytest suites pass.
5. Ruff passes for every Python path enabled in CI.
6. Cloudflare Worker tests and type-check pass.
7. The backend Docker image builds and its `/health` endpoint responds from a container.
8. Generated frontend artifact contract tests pass.
9. No tracked secret, generated test artifact, or local environment file is introduced.
10. Runtime controls and rollback procedures are documented.

## Verification commands

Run from the repository root:

```bash
npm ci
npm run lint --workspace=apps/frontend
npm run type-check --workspace=apps/frontend
npm run test --workspace=apps/frontend -- --run
npm run build --workspace=apps/frontend
npm run test:e2e --workspace=apps/frontend

ruff check apps/backend apps/ml/ml apps/ml/model_trainer_v3.py apps/ml/tests scripts tools
pytest apps/backend/tests apps/ml/tests scripts tools -q

npm ci --prefix workers/refresh-prices
npm run type-check --prefix workers/refresh-prices
npm test --prefix workers/refresh-prices

docker build -f apps/backend/Dockerfile -t quantiv-backend:verify .
```

## Rollout sequence

1. Deploy rate-limit code with conservative limits and the emergency switch documented.
2. Verify Clerk flows, quote refresh, ML fallback, and security headers on a preview deployment.
3. Enable production limits and watch `429` rates through platform logs.
4. Confirm production docs are disabled and `/health` remains publicly available.
5. Promote CI checks to required branch protections after the first green run.
6. Keep production smoke checks alerting-only; they verify deployment state but do not replace pre-merge gates.

## Deferred follow-up

The following are intentionally outside this pass:

- full Content Security Policy enforcement;
- managed error reporting or tracing;
- a replacement database migration framework;
- large frontend component decomposition;
- global test coverage percentage gates;
- staging infrastructure for nightly data commits.

These should be prioritized from production evidence rather than added speculatively.
