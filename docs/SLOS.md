# Production telemetry, freshness SLOs, and alerts

Quantiv separates **service availability** from **quantitative-state freshness**. A healthy HTTP process serving stale forecasts is not healthy production data, and a provider/data hold is not the same incident as a crashed service.

## Managed telemetry surfaces

- **Railway runtime logs/metrics** are the managed backend telemetry sink. Every FastAPI request receives an `X-Request-ID`; the same ID is emitted with method, path, status, and duration. Unhandled exceptions emit `backend_request_exception` with the correlation ID before normal framework error handling. Request bodies, query strings, HMAC values, API keys, cookies, and provider payloads are never logged by this middleware.
- **Vercel runtime logs, Analytics, and Speed Insights** provide the managed frontend/API-route latency/error surface.
- **GitHub Actions + retained receipts/R2 controls** are the batch telemetry surface for refresh, model, Neon import, and publication failures.
- `/health`, the authenticated `/api/ml/status` + `/ml-status` page, `control-plane.json`, control-plane history, signed model receipts, data reconciliation, and frontend release manifests provide inspectable production identity/freshness evidence.

## SLOs and alert thresholds

| Signal | SLO / normal bound | Alert / incident threshold | Evidence |
|---|---|---|---|
| Backend availability | 99.9% successful `/health` over 30d | 2 consecutive failed production-smoke probes or 5xx burst >1%/5m | Railway metrics/logs + `production-smoke.yml` |
| Backend latency | p95 < 500 ms for API requests excluding model activation | p95 >= 1 s for 15m | Railway request records keyed by `request_id` |
| Frontend availability | 99.9% successful production navigation over 30d | Vercel 5xx >1%/5m or production smoke failure | Vercel + production smoke |
| Quote freshness | <= 180 s during the canonical regular-hours window | worker heartbeat/lease stale >180 s; distinguish from off-hours | Redis worker status / quote route diagnostics |
| Earnings calendar | successful daily refresh; near-term calendar refreshed <=36h | latest successful refresh >36h or calendar integrity gate fails | Actions run + calendar/source receipts |
| Options snapshot | current expected market session; tolerated batch gate max lag 5 calendar days | reconciliation/options gate rejects candidate or validated fallback unavailable | `data_reconciliation.json`, `options_snapshot_status.json`, control plane |
| Forecast freshness | refreshed after accepted options snapshot; six supported horizons represented by the active bundle | no fresh forecast/import for >36h or required horizon missing | `/api/ml/status`, forecast validation/import receipts |
| Model identity / age | signed champion identity always resolvable; weekly retrain cadence | signature/bundle mismatch immediately; no successful retrain for >14d | signed champion/registry, model decision/evaluation receipts |
| Daily refresh | one successful run per day | >36h since successful refresh | Actions + `control-plane-history.json` workflow reference |
| Frontend release | immutable release manifest/archive read back before promotion | R2 upload/readback/hash/pointer failure: immediate page | frontend publication receipt/manifest + workflow |
| Neon import | exact active-bundle import succeeds whenever forecasts change | import step fails or active bundle != imported bundle: immediate page | `forecast_import.json`, control-plane `neon_import_status`, `/api/ml/status` |
| Held/degraded state | explicit state, never silent substitution | missing/unknown state or unsafe publication while held: immediate incident | control plane, reconciliation exceptions, options decision receipt |

## Triage contract

1. Start with the request/run correlation ID and identify whether the symptom is **service-down**, **provider/data stale**, **held/degraded by a gate**, **model-control**, or **publication/import**.
2. Preserve the exact Git SHA, workflow run/attempt, R2 release ID, model bundle ID, reconciliation/options receipts, frontend release ID, and Neon activation/import receipts.
3. Never turn a failed freshness gate into a green availability state by silently using unverified bytes. Follow the existing runbooks for safe retry/rollback.
4. Alerts must contain identifiers and statuses, not secrets or raw provider/customer payloads.

The thresholds above are deliberately operational rather than trading claims: Quantiv's production decision scope remains end-of-day research and is not live-trading eligible.
