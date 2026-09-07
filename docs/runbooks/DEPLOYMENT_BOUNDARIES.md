# Deployment boundaries

Each deploy target should consume only the source and immutable artifacts it requires.

- **Vercel frontend:** application source plus an explicitly materialized public research/data release.
- **Railway API:** serving source plus the currently signed model bundle; runtime activation verifies the signed bundle before switching.
- **Railway quote worker:** quote-service source and shared market-session contract; no frontend publication artifacts.
- **Cloudflare worker:** thin trigger/scheduling logic and shared market-session contract; no model or research data.
- **GitHub Actions pipelines:** orchestration plus pipeline dependencies; generated production state is uploaded to the canonical artifact store rather than retained as repository state.

A deployment must not succeed merely because stale generated files happened to be present in the Git checkout.
