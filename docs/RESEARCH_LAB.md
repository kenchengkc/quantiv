# Research Lab

The Research Lab turns Quantiv's point-in-time historical earnings evidence into queryable cohorts. It is designed for calibration and event research, not execution backtesting.

## Evidence boundary

The production research universe is built directly from analytical sources, independently of the per-ticker page history limit. An event is eligible only when the source-level builder can establish:

- a canonical historical earnings identity;
- a finite signed timing-aware realized earnings move from explicit pre/post price endpoints;
- a finite positive historical market-implied move from `v_eligible_straddles`;
- a valid pre-event observation/expiry window for the event session;
- `implied_quality_status = decision_eligible_eod`.

Those historical option observations reuse the same leg, pair, spread, delta, DTE, quote-timestamp and quote-quality gates as the rest of Quantiv. The Research Lab does not reconstruct old implied moves from current quotes.

Realized moves are corporate-action normalized and session aware:

- BMO: previous trading close to event-day/next available close;
- AMC: event-day/previous available close to the next trading close;
- unknown/during-market rows: conservative symmetric bracket (`pre < event`, `post > event`).

Split and cash-dividend controls normalize the post-event price onto the pre-event economic basis. The artifact retains both raw and adjusted endpoints plus the number of applicable split/dividend actions.

### Timing provenance and look-ahead

The canonical earnings table can infer an unknown reporting session from a company's reporting history, including later reported events. That inference is useful for display but is not point-in-time evidence for an old event. The research-universe builder therefore uses BMO/AMC timing only when `timing_source = reported`; inferred timings are deliberately downgraded to the conservative unknown-session rule for historical selection.

### Research cutoff

The production build derives its default source as-of date from the latest options snapshot. Before realized-window selection it replaces `v_ohlcv` with an in-memory snapshot containing only rows on or before that date. A near-cutoff event therefore cannot consume a later close merely because the underlying OHLCV lake is newer than the options evidence.

The same cutoff is applied to research-only retired-company source recovery: retired earnings rows must be strictly before the source as-of date and retired split/dividend rows cannot be after it. The semantic contract independently rejects a realized post-close, retired source row, or corporate-action source date that lies after the declared source as-of date, even if a malformed artifact is resealed with a new hash.

## Historical membership and survivorship control

The forward calendar intentionally removes companies after confirmed delisting. That is correct for current product behavior but is not a valid historical research-universe rule.

`tools/build_research_history.py` therefore re-queries only the explicit retirement ledger in `config/delisted_tickers.json` from the upstream DoltHub earnings source, bounded to events before the research cutoff. Those rows are inserted only into the in-memory research connection. They are never restored to the forward calendar, screener, symbol universe, or forecast path.

The operational corporate-action snapshot is also intentionally scoped to active names. To prevent a retired event from being interpreted as having zero splits or dividends merely because the company left that snapshot, the research build performs the same bounded supplement for retired-company split and dividend history and unions those rows with the verified operational action receipt in memory.

The source-level artifact retains and binds:

- the exact configured retired ticker set;
- normalized retired-company earnings source rows;
- normalized retired split/dividend rows;
- deterministic SHA-256 digests and provider page counts for every retained source set;
- configured retired tickers with no upstream earnings rows;
- the number of research-only earnings rows installed;
- the verified operational corporate-action receipt identity and combined action counts.

A source-level artifact with missing, tampered, out-of-ledger, future-dated, or count-inconsistent retirement evidence fails the public contract gate.

Ticker renames are a separate identity issue rather than a delisting/survivorship issue. The canonical earnings source carries same-company history onto the current symbol, while older option/OHLCV partitions can still use the event-time symbol. Those canonical events remain visible in candidate/exclusion accounting instead of silently disappearing, but event-time alias replay is not claimed complete by this change. A future point-in-time alias layer can recover those observations without altering the retirement control above.

## Build path

```text
analytical earnings + v_eligible_straddles + cutoff-sealed v_ohlcv
        │
        ├── verified corporate-action control
        └── cutoff-bounded retired-company source supplement
                         │
                         ▼
              tools/build_research_history.py
                         │
                         ▼
              public/research-history.json
              source = analytical_duckdb
              completeness = source_level
              universe_id = sha256:...
                         │
                         ▼
             immutable frontend publication
                         │
                         ▼
                 /api/research/cohort
                         │
             ├── content-addressed JSON
             ├── CSV export
             └── /research UI
```

The daily reconciled-data path refreshes this artifact after an accepted options snapshot or a verified options fallback. The writer is atomic: a failed research-universe build does not replace the last validated artifact or weaken the data/forecast gates.

Per-symbol pages may continue to retain only a small recent history for display. That limit no longer defines the production Research Lab universe; a ticker's thirteenth and older eligible events remain available to source-level research.

### Preview migration path

Frontend prebuild preserves an existing source-level artifact verbatim. If an older frontend release has no source-level artifact, `build-research-history.mjs` may construct a temporary symbol-derived preview using schema `quantiv.historical-event-universe.preview.v1`. That preview explicitly declares:

```json
{
  "source": {
    "kind": "display_payload_fallback",
    "completeness": "display_limited"
  }
}
```

A preview has no source-level `universe_id` and must not be described as complete historical coverage. The production source-level contract is `quantiv.historical-event-universe.v1`.

## Source-level artifact and audit

`quantiv.historical-event-universe.v1` carries a deterministic `universe_id` over the full source metadata, retirement-membership evidence, inclusion/exclusion audit and eligible event rows. `generated_at` is operational metadata and is not part of that identity.

Each eligible event retains:

- event identity and timing provenance;
- option observation date, expiry, DTE, lead, ATM evidence, quote timestamps, pair spread and ATM-delta distance;
- signed and absolute realized move;
- exact pre/post price endpoints and corporate-action-normalized post price;
- EPS/revenue surprise values when available;
- evidence source and the number of source rows collapsed into the canonical event.

The audit also retains every canonical event excluded from the eligible cohort with explicit reason codes. Candidate, eligible and excluded counts must reconcile.

The canonical earnings source currently does not expose a reliable row-level availability timestamp for every historical event. Rather than invent one, the artifact records `availability_timestamp = null` and `availability_status = not_available_in_canonical_source`. This is an explicit data-lineage limitation to address if a source with historical publication timestamps becomes available.

## Query contract

Current filters are ticker substring, report session, fiscal quarter, realized move inside/outside implied, EPS beat/miss, implied-move bounds, historical observation lead bounds, sort key/direction, and a bounded returned-row limit. The browser keeps filters in the URL so a cohort can be shared exactly.

The API computes summaries over the full matching cohort before applying the returned-row limit. JSON and CSV exports carry the same content-addressed snapshot identity.

## Content identity

API responses use schema `quantiv.historical-cohort.v1` and receive a `sha256:<hex>` snapshot ID. For a source-level cohort, the ID binds the complete `universe_id`, not only the rows returned to the caller. It also covers source/completeness metadata, current forecast evidence, canonical query, full matching summary/counts, exact ordered returned rows, and decision-scope declarations.

The source-level `universe_id` itself is not accepted on shape alone. Publication validation reconstructs the canonical identity from every field except `universe_id` and operational `generated_at`, recomputes SHA-256, and rejects a stale or forged content address. This makes source/audit/event mutations detectable even when all individual fields remain otherwise semantically valid.

This means unrelated rows omitted from a bounded response are still bound indirectly through the source-level universe identity. Replaying an old source release remains a separate retained-release API capability to implement.

CSV carries the same snapshot ID in the response header and every row. The API also emits `X-Quantiv-Universe-Completeness` so a caller can distinguish `source_level` from a migration preview.

## Contract checks

`tools/validate_public_contracts.py` performs semantic checks for source-level history. It rejects duplicate identities, count mismatches, nonfinite/invalid metrics, arithmetic mismatches, option-session violations, post-cutoff realized windows, future-dated retired source evidence, malformed/tampered retirement evidence, stale `universe_id` content addresses, and any attempt to mark historical cohorts live-trading eligible. The preview contract is explicitly separate and cannot satisfy the source-level path.

The repository also publishes JSON Schema documents for the source-level and preview structures. Executing every generated/API artifact through those JSON Schema documents is a broader contract-engine task; the semantic checks here do not claim that broader task is complete.

## Decision scope

The Research Lab permanently declares:

```json
{
  "decision_scope": "end_of_day_research",
  "live_trading_eligible": false,
  "live_quote_overlay_included": false
}
```

The cohort API does not inherit these declarations from a potentially different current/live control snapshot.

This is historical research evidence, not a fill simulator. It does not model bid/ask execution, intraday option marks, commissions, liquidity/size, slippage, or synchronized order timestamps. A future options-strategy backtester would require those additional point-in-time execution inputs rather than reusing this calibration surface and calling it P&L.
