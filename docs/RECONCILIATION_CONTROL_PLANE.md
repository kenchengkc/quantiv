# Reconciliation control plane

The reconciliation control plane is an exception-first manifest generated from
the local DuckDB views and checked-in reference-data controls. It does not add a
database, cache, queue, or long-running service.

## Current manifest

`scripts/build_data_reconciliation.py` writes
`data/validation/data_reconciliation.json` using the versioned
`quantiv.data-reconciliation.v2` contract. The manifest contains:

- row counts, symbol counts, date ranges, and freshness lag for options, OHLCV,
  and earnings datasets;
- expected upcoming earnings events versus events with a decision-eligible
  option chain, including a bounded missing-chain sample;
- duplicate serving-key counts for the latest 30-day options and OHLCV windows;
- configured rename/delisting counts and any retired source symbol still found
  in a current pipeline view;
- fail-closed option-quote eligibility, including crossed markets, zero or
  non-commercial quotes, excessive spreads, invalid IV, and DTE policy;
- content-addressed split/dividend receipts and event-window continuity;
- rejected-record quarantine artifacts and deterministic replay equivalence;
- source expected-versus-received counts, content hashes, and partition hashes;
- stable critical/warning codes and a deterministic manifest ID.

Critical exceptions make `decision_safe` false and fail the scheduled workflow
before scoring or publication. Sparse exact-horizon coverage is advisory when
aggregate decision coverage remains above its publication floor; the manifest
still records each missing horizon and sample so the exception cannot disappear.
GitHub Actions retains every manifest for 30 days, including failed runs.

## Status semantics

- `passed`: no exceptions.
- `degraded`: no critical exception, but coverage gaps or instrumentation work
  remain. Publication may proceed because `decision_safe` is true.
- `failed`: one or more critical exceptions; strict mode exits nonzero.

## Enforced publication controls

- The newest options partition must reconcile expected and received rows,
  hashes, and replay-equivalent output.
- Quote quality must pass the configured commercial-usability thresholds.
- Rejected contracts are retained in the options quarantine ledger.
- Corporate-action pagination, hashes, replay, and active-universe receipts
  must pass before adjusted realized moves are accepted.
- Retired or renamed-away symbols cannot remain in future earnings views.
- `scripts/apply_ticker_lifecycle.py` reapplies lifecycle changes detected
  after provider synchronization to both earnings CSV and Parquet, and requires
  their event keys to agree before the integrity and reconciliation gates run.

The present decision scope is `end_of_day_research`. A passing reconciliation
manifest does not upgrade date-level options snapshots to live execution data;
`live_trading_eligible` remains false until the source supplies sufficiently
timely quotes and the required liquidity fields.
