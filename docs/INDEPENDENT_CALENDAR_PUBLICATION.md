# Independent calendar publication

Status: implemented in the `cleanup/p0-calendar-reference` change set; pending normal CI/Security and merge.

## Purpose

The earnings calendar and the retained options/model research now have separate publication identities. A fresh, validated reporting date/session can advance while the options snapshot is held, without rescoring forecasts or relabeling retained research as current.

This addresses the supported recovery case where the latest options candidate is rejected and a prior verified options snapshot remains active. Operational completion is not research-publication eligibility: the calendar may advance independently while scoring, forecast validation, Neon forecast import, forecast R2 promotion, and the monolithic research JSON rebuild remain gated by `options_gate.can_score`.

## Implemented release boundary

`scripts/calendar_reference.py` owns a separate content-addressed calendar contract:

- release schema: `quantiv.calendar-reference.v1`;
- receipt schema: `quantiv.calendar-reference-receipt.v1`;
- mutable discovery pointer: `quantiv.current-calendar-reference.v1`;
- event identity: ticker, earnings date, normalized reporting session;
- no option, IV, Greek, model, quantile, straddle, or realized-move calculations;
- deterministic release identity from semantic event content;
- separately content-addressed validation/source receipt;
- source-file SHA-256, universe identity, producer Git revision, and observation time.

The supported display window remains last week through two weeks ahead for the existing frontend symbol universe. Duplicate calendar rows use the same conservative rule as the frontend builder: a row with reported actuals wins; otherwise the latest revised date wins. Unknown sessions remain `unknown` and are never silently converted to AMC.

The default calendar anchor uses `America/New_York`, not the GitHub runner's UTC calendar date. This prevents a run between midnight UTC and midnight New York from advancing the visible week a day early.

## Current-run source evidence and safety checks

A calendar release is publishable only after:

1. a successful current-run DoltHub earnings fetch has written explicit source evidence;
2. the source observation timestamp is at or after `REFRESH_STARTED_AT`;
3. the existing earnings-calendar integrity gate passes against the prior release baseline;
4. required schema/date/session fields are valid;
5. canonical event identities are unique after duplicate resolution;
6. the configured frontend universe is non-empty.

`scripts/sync_dolthub.py --earnings` records the DoltHub source receipt immediately after successfully writing the earnings calendar. The import is safe in both direct-script execution and package/pytest execution. Restored cross-run metadata cannot masquerade as a new fetch because the calendar builder rejects observations older than the current refresh start.

A failed reference build leaves the prior R2 pointer untouched. No warn-only calendar-integrity path grants publication.

## R2 promotion contract

`scripts/r2_push_calendar_reference.sh` publishes:

```text
calendar-reference/releases/<release_id>.json
calendar-reference/receipts/<receipt_id>.json
calendar-reference/current.json
```

Promotion order is fail-closed:

1. upload the immutable release with `--immutable`;
2. upload the immutable receipt with `--immutable`;
3. read both immutable objects back from R2 and byte-compare them;
4. only then upload `current.json`;
5. read the pointer back and byte-compare it;
6. only after remote verification, project the browser-safe `calendar-reference.json` and receipt into the frontend publication workspace.

`scripts/tests/test_calendar_reference_r2.py` uses a fake R2 transport to enforce this exact sequence. Its corruption case proves that a failed immutable readback cannot advance `current.json`.

`scripts/r2_push.sh --skip-forecasts` invokes this publisher with explicit `bash` when `REFRESH_STARTED_AT` is present. Storage-only callers without a daily-refresh timestamp do not acquire this side effect.

## Options-hold behavior

In `.github/workflows/data-refresh.yml`, options reconciliation and fallback verification happen before the unconditional `r2_push.sh --skip-forecasts` step. Therefore both paths can reach calendar publication:

- accepted fresh options candidate; or
- rejected candidate with a successfully verified prior options fallback.

Calendar publication itself does not depend on `can_score`.

The following research operations remain conditional on `steps.options_gate.outputs.can_score == 'true'`:

- `daily_score.py`;
- scored forecast validation and receipt production;
- model monitoring;
- Neon forecast import;
- forecast R2 promotion;
- `tools/build_frontend_data.py` and its research payload regeneration.

That separation is the publication hold: fresh dates/session identity can advance while retained research artifacts remain byte-for-byte unchanged.

The Python acceptance test writes representative retained artifacts (`weekly.json`, `weeks/*`, `screener.json`, `symbols/*`, and `evidence/forecast.json`), performs only the calendar projection, and asserts their exact bytes are unchanged before and after. The TypeScript merge tests separately prove that the retained research object is not mutated.

## Rendering and research isolation

The static homepage and client week navigation share `mergeCalendarReference`.

The calendar reference is authoritative for visible event identity. Research is attached only on an exact match of:

- ticker;
- earnings date; and
- a known normalized session (`bmo`, `amc`, or `dmh`).

A revised date, changed session, unknown session, newly discovered event, or missing research event renders dates/session only. It does not inherit old ML moves, implied moves, quantiles, or realized moves.

When identity matches exactly, the existing validated research object is reused with its original research `as_of_date`. The merged metadata records the calendar release/receipt/observation separately from `research_as_of_date`.

Calendar week caches are keyed by both calendar release ID and week start. SSR bytes are seeded under the week that the server actually rendered; if the client computes a different week at a timezone/week boundary, it takes the cold-fetch path rather than mislabeling server data under the client's key.

## What remains intentionally unchanged

This change does **not**:

- rescore on a fallback options snapshot;
- regenerate screener/symbol research during a hold;
- alter forecast receipts or snapshot IDs;
- change publication thresholds;
- infer reporting sessions;
- make ticker-page research adopt a revised calendar event;
- move calendar publication into a wholly separate GitHub job.

The calendar publisher is independent of the options scoring gate but still executes within the successfully reconciled daily refresh. A future separate job is possible once the calendar receipt alone is sufficient to establish all desired operational prerequisites.

## Acceptance contract

Before merge, the implementation must pass:

- deterministic/content-addressed calendar release tests;
- current-run DoltHub evidence tests in direct/package-safe import paths;
- New York date-boundary test;
- exact-match/dates-only frontend merge tests;
- server/client cache-key mismatch test;
- fake-R2 immutable upload → readback → pointer-last tests;
- byte-for-byte retained-research isolation test;
- normal frontend, backend, data-pipeline, Railway and quote-worker CI;
- Playwright/performance;
- Python and JavaScript/TypeScript CodeQL.

A subsequent real daily refresh is the production exercise: when options are held but calendar checks pass, the new calendar pointer may advance while the retained research release remains unchanged.
