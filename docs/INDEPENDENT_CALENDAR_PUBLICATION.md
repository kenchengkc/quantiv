# Independent calendar publication

Status: proposed next implementation; not yet enabled.

## Why the recovery is healthy but research is held

The September 6 recovery run (`34014440855`) rejected the September 4 options
candidate, restored the September 1 published options snapshot, and advanced
independent data without rescoring forecasts. The retained forecast receipt
passed its September 2 validation. The September 6 control assessment still
blocks new research: restored options are three market sessions behind the
expected September 4 source date, and eligible event coverage is insufficient.
Operational completion is not research publication eligibility.

The remaining coupling is in `daily-refresh.yml`: `Build frontend JSON` runs
only when `options_gate.can_score` is true. `build_frontend_data.py` produces
calendar, screener and symbol research together. Therefore the canonical
earnings CSV can advance while the visible calendar remains tied to a retained
research release. Running that builder on fallback options would break the
recovery boundary, not solve this dependency.

## Release boundary

Introduce a separate, versioned `quantiv.calendar-reference.v1` artifact, with
no forecast, option, IV, Greek, or realized-move calculations. It contains:

- event identity: ticker, earnings date, normalized reporting session;
- reference metadata: company name and explicitly sourced date/session fields;
- source observation time and coverage window, not merely build time;
- deterministic content identity, source-file SHA-256, universe identity,
  producer Git SHA and a linked calendar validation receipt.

Publish an immutable release and a small current pointer. Hash canonical
content (exclude a volatile build timestamp from semantic identity), verify the
receipt/digest before moving the pointer, and preserve previous releases.
Unknown sessions remain unknown; do not silently default them to AMC.

Initially retain the existing configured options-product universe rather than
adding every outside-universe calendar row. Persist its identity independently
of the latest accepted options partition. Calendar membership must not require
a decision-eligible chain: dates-only events are the point of this release.
Use the same canonical duplicate-event resolution as the existing builder.

## Independent safety checks

Calendar publication requires current-run source-fetch evidence, source
freshness, schema/date/session validity, unique canonical event identities,
explicit universe/coverage bounds, and the existing calendar-integrity checks.
A new timestamp on an old CSV is not freshness evidence. A warn-only integrity
override must not silently grant public calendar publication.

Place the initial publication step after successful independent source checks
and accepted-candidate or verified-fallback reconciliation, outside
`can_score`. This releases dates during the supported options-only recovery
path while retaining current non-options safety barriers. It does not yet make
calendar publication independent of every possible workflow failure; a separate
job can follow once the reference receipt is sufficient on its own.

A failed reference check retains the last valid calendar pointer and its actual
observation date. Record the failed attempt separately. Do not stamp the old
reference as freshly observed or silently publish partial data after a fetch
error.

## Rendering and research isolation

The homepage and week navigation consume the reference release for event dates.
Reuse a single pure merge function in static rendering and client navigation;
key caches by calendar release identity as well as week to avoid stale hydration
and cross-week cache contamination.

Research is an optional, separately dated overlay. Join only on exact ticker,
earnings date and known normalized session. A date/session change, unknown
session, absent research event or invalid receipt yields dates-only display:
null model/market metrics, no inherited quantiles and no inferred realized move.
Existing research for a matching event may remain visible only with its original
as-of date and clear retained-EOD scope. Never relabel it as current options.

Keep `weekly.json`, `weeks/*`, `screener.json`, `symbols/*`, forecast receipts and
research snapshot IDs unchanged during calendar-only publication. Do not mutate
the existing screener/symbol contract semantics. Calendar exports, if needed,
must identify the calendar reference separately from immutable research exports.
Dates on ticker pages can later use the same overlay, but must not rewrite the
event identity of a retained forecast panel.

Use a quiet local footer: calendar observation date and, when metrics are
present, research as-of date. Detailed receipt/gate state belongs in Validation
or Evidence, never a new global ribbon.

## Implementation sequence and acceptance tests

1. Add schema, pure reference builder, current-run receipt validation and
   deterministic release tests. Establish source observation evidence before
   wiring publication; reject missing evidence rather than inventing freshness.
2. Add the calendar-only workflow path and separate control status. Test accepted
   options, verified options fallback, invalid fallback, failed source fetch,
   stale reference, integrity failure and pointer rollback/last-good behavior.
3. Wire static homepage and week navigation to the shared merge. Test revised
   dates, BMO/AMC changes, unknown sessions, no chain, week rollover, empty weeks,
   removed events, cache identity and SSR/client parity.
4. Byte-compare all retained research artifacts and snapshot IDs before/after
   a calendar-only run. No scoring, forecast validation, Neon forecast import or
   forecast promotion may execute in this path.
5. Browser/Playwright audit dates-only and retained-metric cards at desktop,
   tablet and mobile widths. Run full CI and Security before merging; verify
   the next actual refresh shows the fresh calendar date independently of the
   held research release.
