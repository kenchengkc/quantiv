# Data Pipeline Recovery

Use this runbook for failed/stale daily refreshes, rejected options candidates, provider gaps, and forecast-to-Neon import failures.

## Trigger symptoms

- `Daily data refresh` fails or exceeds its normal completion window.
- `data_reconciliation.json` fails strict freshness/reconciliation checks.
- `options_snapshot_status.json` says the candidate cannot score or is held/degraded.
- `daily_forecasts.json` validation fails.
- Neon import fails while the R2 data/forecast publication succeeds.
- The public control snapshot shows stale data, degraded forecast publication, or a mismatched provider state.

## Establish current state

Before rerunning anything, preserve/record:

- failing workflow run URL, head SHA, start/end time;
- `data/validation/data_reconciliation.json` artifact;
- `data/validation/options_snapshot_status.json` and quarantined candidate artifact;
- `data/validation/daily_forecasts.json`, if produced;
- current R2 runtime-state/data pointer identities;
- last successful data-refresh run and its production control snapshot;
- current model champion bundle ID;
- provider request/quota status if the failure involves Finnhub/FMP/TwelveData/DoltHub.

Do not delete a quarantined candidate before identifying why it was rejected.

## Failed provider synchronization

1. Determine whether the provider failure is optional/degradable or required for the downstream contract.
2. Confirm the workflow used the intended fallback/hold path rather than continuing with a partial candidate as if it were fresh.
3. If a retry is safe and provider quotas permit it, prefer `workflow_dispatch` of the owning workflow rather than manually copying files.
4. Do not relax freshness thresholds merely to make the retry pass.
5. If the provider remains unavailable, retain the last validated state and surface stale/degraded status according to the existing contract.

A substitute provider must not be introduced during an incident unless field semantics, timestamp/as-of behavior, and reconciliation rules are already defined and tested.

## Rejected/stale options candidate

The options resilience path is intentionally allowed to reject a candidate and restore a previously validated baseline.

1. Inspect the reconciliation manifest and candidate quarantine.
2. Confirm whether rejection was caused by sync failure, row/freshness controls, or candidate timestamp evidence.
3. Verify the fallback baseline itself passes the fallback verification path and was created before the current run's candidate.
4. If scoring is held, leave scoring held. Do not copy the rejected candidate into the live Parquet location.
5. Re-run only after correcting the source/provider condition or control bug.

Recovery is successful when the next candidate passes strict reconciliation or the system remains intentionally degraded against a verified prior release.

## Earnings calendar/source failure

1. Preserve current DoltHub/Finnhub/FMP source-evidence metadata and the prior calendar reference.
2. Confirm the current run recorded fresh source-fetch evidence rather than reusing a previous run's metadata.
3. Run the calendar-integrity gate against the prior retained release.
4. If the source is unavailable, keep the previous independently published calendar reference when the publication contract permits it; do not invent current-run evidence.
5. Verify the frontend calendar identity matches the promoted calendar release before closing the incident.

## Forecast validation failure

If `daily_score.py` completes but forecast validation fails:

- do not publish/import the failed candidate as production evidence;
- inspect model bundle identity, required columns, quantile ordering/calibration/schema controls, and candidate input freshness;
- rerun from a verified input/model state rather than editing the forecast file in place;
- preserve the validation report as incident evidence.

## Neon import failure

Neon is downstream of validated forecast production; an import failure does not make the R2 forecast artifact invalid.

1. Record the exact forecast path and model bundle ID intended for import.
2. Confirm the forecast artifact passed validation and still references the expected champion.
3. Diagnose database connectivity/schema/migration/credential issues.
4. Retry the importer with the **exact validated forecast file** and expected bundle identity; do not import a different "latest" file.
5. Require/import a receipt where the production workflow supports one.

Do not rerun training merely because the database import failed.

## Verification

After recovery:

- strict freshness and reconciliation pass, or the control snapshot explicitly declares an intentional degraded/held state;
- R2 readback/pointer state matches the candidate that passed verification;
- published forecasts reference the expected model bundle;
- Neon contains the exact intended forecast identity when import is required;
- frontend/public control state is internally consistent;
- production smoke passes.

## Evidence to retain

Keep the failed reconciliation/options/forecast reports, relevant workflow logs/artifacts, release/pointer identities, provider evidence, and the successful recovery run. Open a regression issue if a bad candidate advanced farther than the intended control boundary.
