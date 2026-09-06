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

Critical exceptions make `decision_safe` false and prohibit new options-derived
research publication. The daily refresh can retain the published options
snapshot under the bounded fallback policy below; unrelated critical exceptions
still fail the workflow. Sparse exact-horizon coverage is advisory when
aggregate decision coverage remains above its publication floor; the manifest
still records each missing horizon and sample so the exception cannot disappear.
GitHub Actions retains every manifest for 30 days, including failed runs.

## Daily refresh fallback

`scripts/options_snapshot_resilience.py` classifies the current run's candidate
report. Only `options_stale`, `event_quote_coverage_below_limit`, and
`option_quote_quality_below_limit` permit fallback. This does not change the
quote-quality or event-coverage thresholds, nor make a failed report safe.

The fallback anchor is the newest options date in R2's current immutable data
release, not the next-oldest local partition or the download metadata cursor.
Every newer unpublished options partition and its ingestion receipt is retained
in quarantine and removed from the canonical options tree. Corporate-action
controls and DuckDB views are rebuilt against the restored universe.

Before independent data can publish:

- The candidate report must be generated during this refresh, have a consistent
  boolean decision and critical-exception count, and identify the local options
  source date. A crashed reconciliation command cannot reuse an old report.
- The rebuilt fallback report must identify the published options date and must
  contain no critical codes outside the same options-only allowlist. Newly
  discovered corporate-action, source-integrity, or OHLCV failures remain fatal.
- The status receipt records the candidate and active dates, quarantined dates,
  candidate manifest ID, and verified fallback manifest ID. Scoring stays disabled
  even if the restored snapshot now passes the report's quality tests.

On fallback, scoring, forecast validation/publication, Neon forecast import,
model monitoring, and the frontend research rebuild are skipped. Independent
datasets and the control-plane/Validation projection may advance. Existing
forecasts and research payloads retain their original dates and identities.

**Current limitation:** calendar CSV/Parquet can refresh, but the visible calendar
is built by the skipped frontend research build. A green fallback refresh does
not mean the visible calendar or options research is fresh. Decoupling reference
publication must not attach an old forecast to a newly dated earnings event.

### Operational acceptance

After a recovery run, inspect the candidate report, `options-snapshot-decision`
artifact, promoted R2 release, and public Validation projection. Confirm the
active options files and hashes are unchanged from the prior release, rejected
candidates appear only in quarantine, healthy independent data advances, and
no new forecast or research payload was generated. Unit/CI success alone does
not demonstrate that the provider-backed daily workflow recovered.

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
