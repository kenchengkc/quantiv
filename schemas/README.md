# Quantiv public schemas

This directory defines browser-facing and researcher-facing data contracts. Schemas are additive compatibility boundaries, not mirrors of every provider field.

Principles:

- a schema version changes when a consumer-visible meaning or required shape changes;
- additive optional fields do not require a new major contract;
- generated artifacts keep their existing `metadata.version` / `schema` discriminator;
- schemas describe public research state only, not secrets or operational admin payloads;
- point-in-time / decision-scope semantics belong in the contract when they affect interpretation.

Current contracts:

| Contract | Public artifact / surface |
|---|---|
| `quantiv.screener.v1` | `apps/frontend/public/screener.json` |
| `quantiv.symbol-research.v1` | `apps/frontend/public/symbols/<TICKER>.json` |
| `quantiv.dashboard-evidence.v1` | `apps/frontend/public/evidence/forecast.json` |
| `quantiv.control-plane.v2` | `apps/frontend/public/control-plane.json` |
| `quantiv.public-model-validation.v1` | `apps/frontend/public/evidence/model-validation.json` |
| `quantiv.research-snapshot.v1` | `/api/research/screener-snapshot` and `/api/research/symbol-snapshot` |

The schemas intentionally allow additive properties so research payloads can gain optional fields without breaking old clients. Required keys represent the minimum stable contract a consumer can rely on.

`tools/validate_public_contracts.py` checks the committed artifacts against these stable invariants in CI-friendly standard-library Python. The existing `pytest scripts tools -q` data-contract job executes `tools/tests/test_validate_public_contracts.py` on every pull request and every push to `main`, so generated public research state cannot silently drift away from its documented contract.

Dynamic content-addressed snapshots are additionally covered by frontend API/E2E tests because their payload is produced on request rather than committed under `public/`.
