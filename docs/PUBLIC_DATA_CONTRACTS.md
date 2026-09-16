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
| `quantiv.control-plane.v2` | `tools/build_control_plane_snapshot.py` | Validation / protected operational status | current publication cycle |
| `quantiv.public-model-validation.v1` | `tools/build_public_validation.py` | `/validation` | active champion + current control evidence |
| `quantiv.research-snapshot.v1` | research snapshot APIs | notebooks, memos, review records | immutable EOD research state |

## Stable semantics

### Screener

The screener contract identifies its generated version and as-of date and exposes ordered earnings-event research rows. Market-implied, ML, volatility, history, enrichment, and event fields may be added over time. Consumers should not infer live quote freshness from this artifact.

Upcoming generated rows also expose one canonical presentation estimate through `display_forecast_pct`. That value is resolved in a fixed hierarchy: validated ML, strict decision-eligible options math, display-only indicative options, ticker historical median, then a recent universe historical prior. `display_forecast_method`, `display_forecast_as_of`, `ml_status`, `options_status`, `fallback_reason`, and `historical_event_count` describe which evidence produced the display value.

The display field is additive and must not be used to infer analytical eligibility. In particular, `em_ml_pct` remains an actual validated model output only, and `em_straddle_pct` remains a strict decision-eligible options field only. A historical or indicative display fallback never fills or relabels those analytical fields.

### Symbol research

A symbol payload is the complete static research state generated for one ticker. It includes the EOD spot reference, option/straddle research features, earnings history, and any other generated analysis present for that release. The interactive page may overlay a fresher quote, but that quote does not mutate the underlying symbol research contract.

For the published upcoming event, `expected_move.display_forecast_*` carries the same canonical presentation estimate and provenance as the week/screener row. Historical fallback rows are allowed to have no strict options evidence: `straddle_features` remains empty and option-specific fields stay null or absent rather than being synthesized. A genuine spot-updated ML response can temporarily override the static display number in the interactive UI, but it does not rewrite the static contract.

### Display forecast status

`data/validation/display_forecast_status.json` is an operational build artifact, not a browser contract. It records upcoming-event display coverage and the method mix (`ml`, `options_math`, `options_indicative`, `historical`, `historical_prior`). The control-plane projection may expose those metrics separately from strict option-chain coverage.

A build-time invariant requires every upcoming published calendar identity in the generated span to have exactly one finite, positive canonical display forecast. Failure aborts frontend-data publication. This invariant is deliberately separate from strict research eligibility: display coverage can be 100% while strict option coverage is lower.

### Forecast evidence

The dashboard evidence receipt identifies the exact validated forecast release and its coverage/control outcome. It is the browser-safe lineage bridge from public research data back to the validated artifacts that produced it.

### Control plane

The control plane summarizes data, model, release, and exception state. `advisory` is distinct from `failed`: a remaining instrumentation warning can stay publication-eligible, while critical conditions block publication. Standing EOD-research notices (out-of-universe calendar names, chain-wide quote diagnostics, in-universe names without a commercial chain while coverage stays above the floor, and retired symbols successfully excluded from the decision universe) stay on the exception list and do not pull overall status off `passed`. Consumers must inspect `publication_eligible` rather than treating every non-`passed` state as equivalent. Older snapshots may still say `degraded`; treat that as `advisory`.

`generated_at` dates the latest control assessment, not the retained forecast validation. Validation presents these dates separately. A passed retained forecast receipt does not imply that a new research release is eligible. An options-only hold is labeled "Held" in the presentation, with the underlying failed controls and blocked publication still explicit. Other critical controls remain "Blocked". Optional `data.quote_quality_errors` preserves the actual reasons for a composite options gate failure (including freshness); clients must not infer rejection-rate failures from its generic exception code alone.

Display-forecast observability does not participate in research publication eligibility. `data.display_forecast_coverage_pct`, `data.display_forecast_events`, and `data.display_forecast_method_mix` describe presentation coverage only; strict `event_coverage_pct`, data quality, model controls, and `publication_eligible` retain their existing meanings.

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

- reinterpret `advisory` or legacy `degraded` as `failed`;
- call EOD option/ML research “live” because the stock quote is live;
- infer ML availability from `display_forecast_method` without checking `em_ml_pct`/`ml_status`;
- treat `options_indicative` as decision-eligible option evidence;
- strip evidence IDs from exported research state;
- assume a browser rendering is the authoritative source when a generated public artifact exists.

## Enforcement

`tools/validate_public_contracts.py` verifies the committed contract discriminators and stable minimum invariants, including symbol/path identity, screener event counts, evidence receipt shape, control-plane publication semantics, and model-validation horizon/decision-scope consistency.

The frontend-data build additionally executes the stricter generated-publication invariant for canonical display forecasts before writing the final screener/manifest release. This avoids forcing older committed fixture payloads to be retroactively recomputed while still failing closed on every newly generated upcoming publication.

`tools/tests/test_validate_public_contracts.py` runs in the repository's existing data-contract job on pull requests and pushes to `main`. Dynamic snapshot payloads remain covered by frontend unit/API/E2E tests because they are generated at request time.

For the full provenance walkthrough, read `docs/NUMBER_TO_UI.md`.
