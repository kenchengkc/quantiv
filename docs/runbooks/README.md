# Production runbooks

Operational procedures live here. Runbooks describe the current production path; completed migration plans and historical implementation notes belong in Git history or ADRs rather than the active runbook surface.

## Start here

- [Incident response](INCIDENT_RESPONSE.md) — classify, contain, recover, verify, and preserve evidence for production incidents.
- [Data pipeline recovery](DATA_PIPELINE_RECOVERY.md) — daily refresh, provider, options-candidate, forecast-validation, and Neon import failures.
- [Model rollback and serving recovery](MODEL_ROLLBACK.md) — signed champion rollback, serving activation, and exact-bundle forecast recovery.
- [Frontend publication and R2 recovery](FRONTEND_R2_RECOVERY.md) — immutable frontend/runtime releases, pointer/materialization failures, and R2 rollback.
- [Secret rotation](SECRET_ROTATION.md) — planned rotation, exposure response, R2/provider/database credentials, and model-signing trust-root changes.

## Control-boundary references

- [Artifact materialization](ARTIFACT_MATERIALIZATION.md)
- [Deployment boundaries](DEPLOYMENT_BOUNDARIES.md)
- [Rollback boundary](ROLLBACK_BOUNDARY.md)
- [Security boundaries](SECURITY_BOUNDARIES.md)
- [CI scope](CI_SCOPE.md)

## Operating rules

Across all incidents:

1. Establish exact release/data/model identities before changing state.
2. Prefer retaining or restoring the last validated release over weakening a gate.
3. Never overwrite immutable artifacts or signed bundles in place.
4. Treat stale-but-explicitly-degraded state differently from apparently fresh but unverifiable state.
5. Verify recovery at the externally served surface and at the relevant control-plane boundary.
6. Preserve failed receipts/logs/artifact identities long enough to explain why the failure escaped or was correctly contained.
7. Turn any escaped failure mode into a regression test or machine-readable gate.
