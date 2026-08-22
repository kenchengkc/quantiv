# Reconciliation control plane

The reconciliation control plane is an exception-first manifest generated from
the local DuckDB views and checked-in reference-data controls. It does not add a
database, cache, queue, or long-running service.

## Current manifest

`scripts/build_data_reconciliation.py` writes
`data/validation/data_reconciliation.json` using the versioned
`quantiv.data-reconciliation.v1` contract. The manifest contains:

- row counts, symbol counts, date ranges, and freshness lag for options, OHLCV,
  and earnings datasets;
- expected upcoming earnings events versus events with a decision-eligible
  option chain, including a bounded missing-chain sample;
- duplicate serving-key counts for the latest 30-day options and OHLCV windows;
- configured rename/delisting counts and any retired source symbol still found
  in a current pipeline view;
- corporate-action observation coverage;
- quarantine and replay-control status;
- stable critical/warning codes and a deterministic manifest ID.

Critical exceptions make `decision_safe` false and fail the scheduled workflow
before scoring or publication. Coverage gaps and explicitly unfinished controls
are warnings: they remain visible without pretending that the control exists.
GitHub Actions retains every manifest for 30 days, including failed runs.

## Status semantics

- `passed`: no exceptions.
- `degraded`: no critical exception, but coverage gaps or instrumentation work
  remain. Publication may proceed because `decision_safe` is true.
- `failed`: one or more critical exceptions; strict mode exits nonzero.

The first version deliberately marks three areas as incomplete:

- corporate actions are collected, but split/dividend continuity is not yet a
  publication gate;
- bad records fail closed, but are not retained in a quarantine ledger;
- deterministic serving keys and conflict-safe upserts exist, but replay output
  equivalence is not yet tested automatically.

Those gaps are the next reconciliation work items. Recording them now prevents
an attractive control-plane UI from overstating what the pipeline guarantees.
