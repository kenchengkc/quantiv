# Public research data contracts

Quantiv exposes browser-facing research artifacts that are also useful to notebooks, external analysis, QA, and due diligence. These payloads are versioned contracts rather than accidental frontend implementation details.

## Contract policy

A consumer may rely on required fields and their documented meaning for the lifetime of a contract version.

A new version is required when Quantiv changes a required field, changes the meaning or unit of an existing field, changes point-in-time semantics, or broadens a research-only surface into a different decision scope. Additive optional fields are compatible within the same version.

Public schemas live in `schemas/`. Runtime/committed invariant checks live in `tools/validate_public_contracts.py` and execute through the existing data-contract CI suite.

## Contract matrix

| Contract | Producer | Primary consumer | Freshness boundary |
|---|---|---|---|
| `quantiv.screener.v1` | `tools/build_frontend_data.py` | Screener + research export | validated EOD build |
| `quantiv.symbol-research.v1` | `tools/build_frontend_data.py` | Symbol research page + export | validated EOD build |
| `quantiv.dashboard-evidence.v1` | forecast validation/public projection | Evidence surfaces | validated forecast release |
| `quantiv.control-plane.v2` | `tools/build_control_plane_snapshot.py` | global research status / validation | current publication cycle |
| `quantiv.public-model-validation.v1` | `tools/build_public_validation.py` | `/validation` | active champion + current control evidence |
| `quantiv.research-snapshot.v1` | research snapshot APIs | notebooks, memos, review records | immutable EOD research state |

## Stable semantics

### Screener

The screener contract identifies its generated version and as-of date and exposes ordered earnings-event research rows. Market-implied, ML, volatility, history, enrichment, and event fields may be added over time. Consumers should not infer live quote freshness from this artifact.

### Symbol research

A symbol payload is the complete static research state generated for one ticker. It includes the EOD spot reference, option/straddle research features, earnings history, and any other generated analysis present for that release. The interactive page may overlay a fresher quote, but that quote does not mutate the underlying symbol research contract.

### Forecast evidence

The dashboard evidence receipt identifies the exact validated forecast release and its coverage/control outcome. It is the browser-safe lineage bridge from public research data back to the validated artifacts that produced it.

### Control plane

The control plane summarizes data, model, release, and exception state. `degraded` is distinct from `failed`: an advisory coverage/drift condition can remain publication-eligible, while critical conditions block publication. Consumers must inspect `publication_eligible` rather than treating every non-`passed` state as equivalent.

### Public model validation

The model-validation contract reports horizon-level out-of-sample model-vs-straddle statistics, quantile/interval calibration, protocol declarations, model-source lineage, and the current evidence envelope. It is predictive-validation evidence, not strategy P&L.

The contract permanently declares:

```json
{
  "decision_scope": "end_of_day_research",
  "live_trading_eligible": false
}
```

Changing that meaning requires a new contract version and a materially different execution-data/control architecture.

### Research snapshots

`quantiv.research-snapshot.v1` is content-addressed. The SHA-256 ID is derived from canonical research state and evidence, so identical state produces an identical ID.

Current kinds are:

- `earnings_screener`
- `symbol_research`

Snapshots explicitly exclude ephemeral live quote overlays; symbol snapshots also exclude latest-spot re-scoring overlays. The artifact therefore remains reproducible rather than mixing static EOD evidence with an unpersisted intraday observation.

## Compatibility expectations

Clients should:

1. check the contract discriminator/version before parsing;
2. tolerate additive unknown fields;
3. use explicit units/semantics rather than field-name guesses;
4. preserve `as_of_date`, receipt IDs, and snapshot IDs in downstream notebooks or memos;
5. keep live/fresh quote data separate from EOD research artifacts unless the live observation has its own timestamped point-in-time contract.

Clients should not:

- reinterpret `degraded` as `failed`;
- call EOD option/ML research “live” because the stock quote is live;
- strip evidence IDs from exported research state;
- assume a browser rendering is the authoritative source when a generated public artifact exists.

## Enforcement

`tools/validate_public_contracts.py` verifies the committed contract discriminators and stable minimum invariants, including symbol/path identity, screener event counts, evidence receipt shape, control-plane publication semantics, and model-validation horizon/decision-scope consistency.

`tools/tests/test_validate_public_contracts.py` runs in the repository's existing data-contract job on pull requests and pushes to `main`. Dynamic snapshot payloads remain covered by frontend unit/API/E2E tests because they are generated at request time.

For the full provenance walkthrough, read `docs/NUMBER_TO_UI.md`.
