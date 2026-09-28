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

## Refresh scheduling and provider cutoff

The provider-backed daily refresh still has the same hard market-session boundary:
normal runs are rejected if they start at or after 09:00 Eastern on a trading day,
and every admitted shell step is terminated at 09:35 Eastern. Optional non-price
Finnhub enrichment still skips from 09:25 Eastern to reserve live-quote capacity.
Do not add `--allow-market-hours` to a normal refresh.

The primary clock is the Cloudflare Worker under
`workers/daily-refresh-scheduler/`. It dispatches the existing GitHub workflow
at 02:00 America/New_York and checks again at 02:10. The native GitHub
`schedule` remains enabled at 02:17 Eastern as a backup because GitHub scheduled
workflows can be delayed or dropped; the off-minute backup also avoids the
platform's documented top-of-hour congestion window. A workflow-level daily claim selects the earliest normal
run created for each Eastern calendar date; later externally dispatched or native
scheduled runs skip before provider work begins. Actual refresh/recovery jobs use
a separate execution lock so publication writes cannot overlap.

The 09:35 cutoff is intentionally not moved to compensate for dispatch jitter.
The scheduler fixes the clock source; the market-data budget boundary remains the
same.

## Provider-free recovery after an interrupted refresh

A provider-free recovery is the only deliberate exception to the whole-job 09:35
deadline. Use it only when a normal run already completed provider ingestion,
promoted an atomic R2 data release, and then failed during scoring/publication.

The recovery mode:

- requires the exact promoted `data-release` ID and reconciliation
  `manifest_id` from the interrupted run as inputs;
- materializes that release from R2 and verifies every file digest;
- materializes the last Git-pinned, verified frontend release so generated
  artifacts that are intentionally absent from a clean checkout (including
  `research-history.json`) have a trusted baseline;
- restores the saved reconciliation/options decision and the independently
  published calendar-reference release;
- accepts only an `accepted` options snapshot with zero critical exceptions or
  a previously verified fallback whose `refresh_scoring_allowed` flag is true;
- removes all market-data provider credentials from child commands;
- disables TwelveData fallback even if a key exists in the runner environment;
- validates and preserves the existing source-level `research-history.json`
  instead of re-querying retired-symbol providers;
- reruns scoring, forecast validation, Neon import, frontend generation,
  forecast publication receipts, runtime-state publication, and public-contract
  validation from the saved data.

It does **not** rerun DoltHub/Finnhub/FMP/Alpha Vantage/TwelveData/Polygon/Alpaca
market-data fetches, does not refresh market caps, and does not rebuild research
history from upstream sources.

Dispatch `Daily data refresh` manually with:

- `refresh_mode = provider-free-recovery`
- `recovery_release_id = <exact promoted release id>`
- `recovery_manifest_id = <exact reconciliation manifest id>`

If exact release identity or saved decision evidence cannot be verified, recovery
fails closed. In that case wait for the next provider-safe window rather than
loosening freshness or market-hours controls.


1. Determine whether the provider failure is optional/degradable or required for the downstream contract.
2. Confirm the workflow used the intended fallback/hold path rather than continuing with a partial candidate as if it were fresh.
3. If a retry is safe and provider quotas permit it, prefer `workflow_dispatch` of the owning workflow rather than manually copying files.
4. Do not relax freshness thresholds merely to make the retry pass.
5. If the provider remains unavailable, retain the last validated state and surface stale/degraded status according to the existing contract.

A substitute provider must not be introduced during an incident unless field semantics, timestamp/as-of behavior, and reconciliation rules are already defined and tested.

## Rejected/stale options candidate

The options resilience path is intentionally allowed to reject a candidate and restore a previously validated baseline.

Aggregate upcoming-event coverage requires 65% eligible option chains. The
separate per-horizon coverage warning remains at 70%; individual quote eligibility,
source freshness, and model-promotion controls are unchanged. See the
[September 2026 policy evaluation](../research/OPTIONS_COVERAGE_POLICY_2026_09_28.md)
for the historical comparison and its model-performance limitations. A policy
change requires a new normal reconciliation run; never relabel an old held receipt.

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

## Frontend forecast-component mismatch

The daily generator and forecast restoration must complete before publication.
Restoration selects the frozen headline forecast separately from its option
comparison snapshot and historical context. Eligible option percentages move
with their observation date, expiry, strike, and other snapshot fields; they
must not inherit unrelated ML ranking timestamps. Same-day or post-event legacy
option observations are not eligible for restoration.

Every active reported event is synchronized back to its symbol payload, even
when its calendar row was already correct. This is necessary because a later
daily rebuild can replace the symbol projection independently. The restoration
command validates forecast parity after writing. The daily workflow then runs
all public-contract checks before committing generated files, and CI checks the
actual committed corpus. The publisher retains its independent validation gate.

For recovery, use `scripts/restore_reported_forecasts.py --apply --today YYYY-MM-DD`
with the affected release's date, then run `tools/validate_public_contracts.py`.
Review the generated diff and preserve frozen forecast identities and realized
results. After merging a verified repair, dispatch `frontend-publication.yml`;
this publishes the existing corpus without rerunning market-data provider calls.
Verify the live release manifest and the affected calendar and symbol files.
