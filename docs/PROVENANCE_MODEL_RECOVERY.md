# Provenance-invalid model recovery

This is an operator recovery procedure, not a training or fresh-research
publication exemption. A model can pass statistical validation while its
promotion has invalid data provenance. Performance-triggered rollback must not
be used to wait out such an incident.

## September 6 incident

Pre-fix retraining promoted
`458a6c47ec77ed269f093179572cfa18d41ce206e23f842bf55301475546e1e0`
from the retained September 1 options release while publication was held against
the expected September 4 session. Its signed previous champion was
`4d1ab76dc3401072f1c9a809c71aa76d0c905e5117f7d98d31928219a79a8b78`.
PR #83 prevents new retraining through that held-release path; it does not undo
the earlier promotion. This document records the intended recovery, **not evidence
that production has already been restored**.

## Preconditions and sequence

1. Merge the recovery implementation only after repository CI and Security pass.
2. Read the current signed R2 pointer and registry and verify both complete model
   bundles. The expected current identity is a race guard; the only permitted
   target is the signed previous identity. Stop if either has changed.
3. Dispatch **Daily data refresh** on `main`, with `provenance_rollback=true`,
   `run_refresh=false`, `run_retrain=false`, the two exact identities, and an
   incident-specific `rollback_reason`. For this incident use a reason such as
   “September 6 held-release retraining bypass: restore provenance-valid champion;
   retain September 4 publication hold.” Normal refresh, training, and profile
   sweep jobs are disabled for this dispatch.
4. The job restores and verifies the retained raw release, verifies authorization
   before scoring, and rescores with the signed previous model. Both the workflow
   and mutation command run the standard forecast validator. Every forecast row
   must name the target, without null identities. No new model is trained.
   A read-only Neon preflight requires replacements for every upcoming forecast
   key attributed to the rejected bundle, before the first production mutation.
5. Sign and verify the replacement controls before replacing local forecasts.
   Preserve the old signed controls and forecast files under
   `data/models/provenance_recovery/<incident-identity>/`. The invalid bundle is
   retained as evidence but removed from both previous/challenger roles so the
   automatic outcome monitor cannot promote it again as a fallback.
6. Re-read and compare remote model controls, data pointer, and reconciliation
   against the restored originals. Reuse `r2_push.sh --model-recovery` to publish
   models, control registry, and forecasts, with the signed champion pointer last.
   This mode cannot build, upload, or promote a raw-data release or reconciliation.
7. Use `activate_model_bundle.py` for exact-bundle Railway activation and its
   all-horizon preflight receipt. Only then use `import_recent_to_postgres.py`
   with `--full`, `--require-database`, the exact bundle, and activation receipt.
8. Re-read signed R2 controls; verify content-addressed activation/import receipts
   and the exact forecast hash. Read Neon in a read-only transaction to verify
   every restored primary key, the latest import, and absence of upcoming rows
   from the rejected bundle. Byte-compare the data pointer and reconciliation
   before/after. Save all evidence in the `provenance-model-recovery` run artifact.

## Failure handling and limitations

- This uses the existing `daily-refresh` concurrency group to exclude other
  refresh/retrain jobs. The pre-push comparison detects changed remote controls
  but is not a global object-store compare-and-swap. Do not run independent R2
  writers during recovery.
- R2, Railway, and Neon do not form one atomic transaction. If a downstream step
  fails, stop and inspect the uploaded evidence and actual identities. Do not
  blindly re-run the whole job: after pointer promotion, the original current
  guard must fail. Complete only the failed activation/import step using the
  preserved validated decision and receipts after rechecking current identities.
- A verification failure on remaining rejected upcoming forecast rows requires
  investigation of uncovered keys. This job does not silently delete historical
  forecasts or claim a partial restoration succeeded.
- The restored model has no eligible previous/challenger fallback until a normal,
  provenance-valid promotion establishes one. Missing fallback is preferable to
  automatically restoring the disqualified model.
- The rescored forecast remains based on retained options evidence. Recovery
  does not make that evidence fresh or authorize a new research release. Leave
  the publication hold and normal freshness/quality thresholds unchanged.
