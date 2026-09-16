# Display Forecast Fallback Hierarchy

Date: 2026-09-16
Status: Approved design
Branch: `feature/display-forecast-fallbacks`

## Summary

Quantiv will guarantee a user-facing expected-move estimate for every published upcoming earnings event while preserving strict research and ML quality controls.

The presentation layer resolves a canonical display forecast using this hierarchy:

1. Validated ML expected move.
2. Decision-eligible options math.
3. Indicative options math from a structurally valid but lower-quality same-strike pair.
4. Ticker-specific historical median absolute earnings move.
5. Universe-level historical prior.

The display fallback hierarchy is intentionally separate from the research eligibility hierarchy. Indicative options, historical fallbacks, and the broad prior must never feed ML training, ML scoring, model validation, benchmark evaluation, or decision-eligible options coverage.

The main product invariant is:

> Every published upcoming earnings event has a finite positive `display_forecast_pct`, while `em_ml_pct` remains populated only when LightGBM actually produced a forecast.

## Goals

- Eliminate uninformative `—` expected-move cells for upcoming published earnings events.
- Preserve strict ML/data-quality gates.
- Distinguish ML, validated options, indicative options, and historical estimates without adding horizontal clutter to the homepage calendar.
- Make forecast method and fallback reason explicit in public payloads instead of inferring state from null fields.
- Keep calendar, screener, watchlist, and ticker pages consistent by resolving the display forecast once in backend payload generation.
- Preserve point-in-time correctness for historical events and revised earnings dates.
- Improve diagnostics and observability around fallback usage.

## Non-goals

- Loosening ML quote-quality gates.
- Replacing the existing LightGBM model or validation process.
- Treating historical or indicative estimates as calibrated ML forecasts.
- Synthesizing Greeks, IV, quantiles, or option analytics when supporting evidence does not exist.
- Building a sector- or market-cap-conditioned historical prior in this change.
- Carrying an old ML forecast forward as the current homepage estimate for an upcoming event.

## Forecast hierarchy

For each published upcoming earnings event, resolve the display forecast in this order.

### 1. ML

Use the current validated `em_ml_pct` for the exact `(ticker, earnings_date)` event identity.

Requirements:

- Forecast exists for the exact event date.
- Existing model/control-plane requirements pass.
- Existing public ML fields retain their current meaning.

Result:

- `display_forecast_method = "ml"`
- `display_forecast_pct = em_ml_pct`
- `ml_status = "available"`
- `display_forecast_as_of` is the ML snapshot/scoring date represented by the published forecast metadata.

### 2. Decision-eligible options math

If ML is unavailable but the existing strict options pipeline produces an earnings-spanning decision-eligible straddle, use the normal options expected move.

The existing strict quote-quality policy and `v_eligible_straddles` semantics remain unchanged.

Result:

- `display_forecast_method = "options_math"`
- `options_status = "decision_eligible"`
- `display_forecast_pct` uses the current straddle-implied percentage.
- `display_forecast_as_of = as_of_date` for the options snapshot.
- `ml_status` records why ML was unavailable.

### 3. Indicative options math

If no strict decision-eligible straddle exists, search the current options snapshot for a best-effort same-strike call/put pair that spans the earnings event.

Indicative display eligibility is separate from ML decision eligibility.

Structural invalidity is never relaxed. A candidate is ineligible for display if any of these apply:

- Call or put is missing.
- Call and put do not share the same strike and expiry.
- Bid or ask is null, non-finite, or negative.
- Ask is below bid.
- Combined midpoint is non-positive or non-finite.
- Strike is non-positive.
- Expiry does not span the earnings event according to session rules.
- Expiry is outside the configured post-event search window.

Initial display-quality policy:

```json
{
  "max_leg_relative_spread": 1.00,
  "max_straddle_relative_spread": 0.75,
  "max_atm_delta_distance": 0.60,
  "max_post_event_expiry_days": 30
}
```

These values are intentionally separate from `config/option_quote_quality.json` and live in a new checked-in display policy file. The implementation must use these as the initial production defaults. Historical analysis may motivate a later explicit config change, but implementation must not silently alter the thresholds.

Pair selection:

1. Enumerate all structurally valid expiries that span the event and fall inside the configured post-event window, in ascending expiry order.
2. For each expiry, build same-strike call/put candidates and remove structurally invalid pairs.
3. Compute ATM relevance. When both deltas are valid, use:

   `abs(call_delta - 0.5) + abs(put_delta + 0.5)`

4. If valid deltas are unavailable, use strike-to-spot moneyness distance:

   `abs(strike / spot - 1)`

5. Within an expiry, rank candidates by:
   - ATM relevance,
   - combined straddle relative spread,
   - worst individual-leg relative spread,
   - strike proximity to spot,
   - strike as a deterministic final tie-breaker.
6. Select the best candidate that satisfies the display-quality ceilings. If an expiry has no display-eligible pair, continue to the next spanning expiry rather than immediately falling back to history.
7. Use the first expiry in ascending order that contains a display-eligible pair.

Expected-move calculation:

- `call_mid = (call_bid + call_ask) / 2`
- `put_mid = (put_bid + put_ask) / 2`
- `straddle_mid = call_mid + put_mid`
- `display_forecast_pct = straddle_mid / spot`

Use current EOD spot when available. If the source lacks underlying spot, use the existing explicit EOD spot proxy already employed by the research pipeline. Strike must not silently replace a valid spot value.

IV-based move may be retained as supporting detail but must not override a usable straddle midpoint.

Result:

- `display_forecast_method = "options_indicative"`
- `options_status = "indicative"`
- `display_forecast_as_of = as_of_date`
- `fallback_reason = "quote_quality"` when strict eligibility failed only because of decision-quality rules.

### 4. Ticker historical median

If there is no display-eligible options pair, use the median absolute realized move from the most recent four prior earnings events.

Requirements:

- Only events strictly before the target event may contribute.
- Realized moves use the existing timing-aware OHLCV bracket.
- At least two valid prior realized events are required.
- Use up to four most recent valid prior events.

Calculation:

`median(abs(realized_move_pct))`

Result:

- `display_forecast_method = "historical"`
- `options_status = "unavailable"`
- `historical_event_count` is 2-4.
- `display_forecast_as_of` is the forecast cutoff/as-of date, not the date of the latest realized event.
- `fallback_reason` reflects why options failed, such as `no_same_strike_pair`, `no_event_expiry`, or `quote_quality`.

### 5. Universe historical prior

If ticker-specific history contains fewer than two valid prior events, use a versioned broad historical prior.

The prior is the median absolute realized earnings move across Quantiv's active research universe over the trailing two years as of the forecast cutoff.

Prior artifact fields:

- `as_of_date`
- `window_start`
- `window_end`
- `median_abs_move`
- `event_count`
- `symbol_count`

The daily publication path emits the prior for the current forecast cutoff. Historical reconstruction must use the same prior calculation at the historical cutoff; it must not reuse a later prior artifact.

Result:

- `display_forecast_method = "historical_prior"`
- `display_forecast_as_of` is the forecast cutoff.
- `fallback_reason = "insufficient_ticker_history"`

## ML status semantics

`ml_status` is independent from the selected display method:

- `available`: exact validated ML forecast exists.
- `unavailable_inputs`: model infrastructure exists but the event could not be scored with acceptable current inputs.
- `unavailable_model`: champion/model service or required model artifact was unavailable for this event/horizon.
- `unavailable_event`: no exact ML forecast exists for the current published event identity, including a revised earnings date keyed differently from a prior forecast.

The compact UI only needs `ML unavailable`; detailed diagnostics and logs may use the specific status.

## Earnings-session and expiry semantics

Retain the existing event timing rules:

- AMC / after-close: the EOD observation may be from the event date and expiry must be strictly after the earnings date.
- BMO / non-AMC: the EOD observation must be before the event and expiry may be on or after the earnings date.

Indicative selection uses the same temporal semantics as strict options math. A generic near-term straddle that does not span the earnings event must never stand in for an earnings expected move.

## Canonical backend resolver

Introduce one backend resolver for all display forecast decisions, conceptually:

```python
resolve_display_forecast(
    conn,
    ticker,
    earnings_date,
    timing,
    as_of_date,
    ml_forecast,
    universe_prior,
) -> DisplayForecast
```

The resolver owns the hierarchy and returns a structured object containing at least:

```text
pct
method
as_of
ml_status
options_status
fallback_reason
historical_event_count
selected_options_details
```

Week payload generation, screener generation, symbol-detail generation, and watchlist-facing data consume the same resolver output. Frontend code must not independently reimplement the fallback hierarchy.

## Public payload contract

Keep existing `em_*` fields for backward compatibility and analytical purity.

Do not populate `em_ml_pct` with options or historical values.

Add canonical display fields to event payloads:

```ts
display_forecast_pct: number;

display_forecast_method:
  | "ml"
  | "options_math"
  | "options_indicative"
  | "historical"
  | "historical_prior";

display_forecast_as_of: string | null;

ml_status:
  | "available"
  | "unavailable_inputs"
  | "unavailable_model"
  | "unavailable_event";

options_status:
  | "decision_eligible"
  | "indicative"
  | "unavailable";

fallback_reason:
  | "quote_quality"
  | "no_same_strike_pair"
  | "no_event_expiry"
  | "insufficient_ticker_history"
  | null;

historical_event_count: number | null;
```

The same method/status metadata belongs in the ticker's `expected_move` payload so the dashboard can render provenance consistently.

For upcoming published calendar events, `display_forecast_pct` is finite and greater than zero.

## Symbol detail behavior

`build_symbol_detail()` must no longer return `None` merely because there are no strict eligible option pairs.

A ticker page can still be built from:

- published calendar identity,
- spot data when available,
- historical earnings data,
- canonical display forecast,
- provider enrichments.

The expected-move percentage headline must render without requiring a positive spot value. Dollar ranges, scenario analysis, and options-specific panels remain spot/evidence dependent.

Options-specific structures remain empty/absent when evidence is unavailable. Detailed options panels must not receive fabricated fields.

## Homepage calendar UX

Do not add method badges, icons, or extra columns next to expected-move percentages.

The number stays in the existing footprint.

Method differentiation uses subtle semantic text treatment:

- ML: primary text and strongest weight.
- Decision-eligible options: secondary accent and slightly lighter weight.
- Indicative options: same accent family with lower emphasis.
- Historical: muted neutral treatment.
- Historical prior: most subdued readable treatment.

Define semantic CSS variables/classes rather than scattering hard-coded method-specific values through `EarningsGrid.tsx`.

Color is supplemental; meaning remains accessible through tooltip text.

## Expected-move hover UX

The compact expected-move hover is the explicit provenance surface.

ML example:

```text
ML forecast       ±4.7%
Market implied    ±8.1%
Typical           3.2-6.9%
```

Indicative options example:

```text
Market implied    ±8.3%
ML forecast       Unavailable
```

Historical example:

```text
Historical median ±2.9%
ML forecast        Unavailable
Market implied     Unavailable
```

Historical prior example:

```text
Historical prior  ±5.8%
ML forecast        Unavailable
Market implied     Unavailable
```

Use `ML unavailable`, not `ML not working`.

The hover remains compact and does not expose implementation-detail warnings such as exact spread failures.

## Ticker dashboard UX

The ticker dashboard is the detailed provenance surface.

### ML

Show ML as the primary expected move, current options implied move as supporting evidence, model snapshot metadata, and existing quantile/range analytics.

### Validated options

Show options-implied estimate as the primary expected move and clearly state `ML unavailable`.

### Indicative options

Show options-implied estimate as the primary expected move and clearly state `ML unavailable`.

Add a subdued explanation:

> Indicative market estimate; current option quotes did not meet the stricter ML-input quality threshold.

### Historical

Show the historical estimate and sample basis:

> Median absolute move across the last N earnings events.

Also show:

- `ML unavailable`
- `Market-implied estimate unavailable`

Options-only panels remain absent when current options evidence does not exist.

### Historical prior

Show `Historical prior` and explain that company-specific history was insufficient.

No fake Greeks, IV, term structure, options scenario analysis, or ML prediction intervals may be created for historical fallbacks.

## Screener and watchlist

Screener and watchlist use `display_forecast_pct` for their compact expected-move number.

They must not independently fall back through `em_ml_pct`, `em_straddle_pct`, and `em_iv_pct`.

Method-based styling may be reused where layout permits, but consistency of the value and method takes priority.

## Historical event behavior

Already-reported events preserve the actual pre-event forecast that was published whenever possible.

Do not recompute an expected move after the earnings event using post-event information.

If a historical event must be reconstructed, every fallback input, including ticker history and universe prior, must be recomputed at that historical cutoff. Later earnings, later option snapshots, and later priors may not leak backward.

The existing reported-event preservation behavior remains conceptually correct and is adapted to preserve the new display metadata too.

## Revised earnings dates

Forecast identity remains keyed to exact `(ticker, earnings_date)`.

A forecast for a superseded event date must not be attached to a revised calendar date.

For a revised upcoming date, the resolver runs against the current published identity and derives a new options or historical fallback when exact ML is unavailable.

## Control plane and reconciliation

Strict decision-quality coverage remains unchanged.

An event shown with `options_indicative`, `historical`, or `historical_prior` still counts as missing from decision-eligible option coverage when appropriate.

Add display coverage metrics alongside, not instead of, research-quality coverage:

```text
display_forecast_coverage:
  published_upcoming_events
  with_display_forecast
  coverage

method_mix:
  ml
  options_math
  options_indicative
  historical
  historical_prior
```

A healthy example may therefore report:

- Research-quality options coverage: 73%.
- User-facing display forecast coverage: 100%.

Add warning/degraded observability for abnormal fallback mix growth, especially `historical` and `historical_prior`, without redefining the existing ML publication gate.

Reconciliation/debug artifacts retain the selected indicative pair and strict rejection reason so a PAYX-like event can be diagnosed without manually reconstructing raw chains.

## Build-time invariant

Frontend-data generation fails before publication if an upcoming event in `calendar-reference.json` lacks a valid display forecast.

For every upcoming published event:

- matching week event exists,
- `display_forecast_pct` is finite,
- `display_forecast_pct > 0`,
- `display_forecast_method` is one of the allowed enum values,
- method/status combinations are internally consistent.

This makes `never a dash` a data-contract guarantee rather than a React fallback.

Reported events should preserve their original display forecast and provenance. If an older reported row lacks one, reconstruction is allowed only through the point-in-time-safe historical rules above.

## Logging and observability

Replace the current `published calendar rows with NO expected move` outcome for upcoming rows with method-resolution summaries.

Example build log:

```text
forecast methods:
  ML                   30
  options math          4
  indicative options    3
  historical            3
  historical prior      1

ML unavailable:
  quote quality         7
  event/date mismatch   2
  model coverage        2
```

Retain strict quote-quality diagnostics separately.

## Configuration

Add a separate checked-in display-quality policy:

`config/option_display_quality.json`

This policy must not be merged into `option_quote_quality.json`, because the latter is the strict research/ML acceptance contract.

Initial display policy:

```json
{
  "schema": "quantiv.option-display-quality.v1",
  "max_leg_relative_spread": 1.0,
  "max_straddle_relative_spread": 0.75,
  "max_atm_delta_distance": 0.60,
  "max_post_event_expiry_days": 30,
  "min_ticker_history_events": 2,
  "ticker_history_window_events": 4,
  "universe_prior_window_days": 730
}
```

## Testing strategy

### Backend unit/integration cases

Cover at least:

1. ML + valid strict options -> `ml`.
2. No ML + strict eligible options -> `options_math`.
3. PAYX-like 51.75% leg spread -> `options_indicative`.
4. CBRL-like moderately wide ATM pair -> indicative when display policy passes.
5. FUL-like ~190% leg spread -> options rejected; ticker history used.
6. No same-strike pair -> historical.
7. Nearest spanning expiry has no display-eligible pair but a later expiry does -> later expiry is selected.
8. Only one valid prior earnings move -> universe prior.
9. Crossed or negative quote -> never indicative.
10. AMC expiry on earnings date -> reject; expiry must be after.
11. BMO/non-AMC expiry on earnings date -> allowed.
12. Exact revised calendar identity -> no reuse of superseded ML forecast.
13. Upcoming published event -> non-null positive display forecast.
14. Fallback method -> `em_ml_pct` remains null.
15. Historical fallback -> no fabricated options fields.
16. Reported-event preservation retains method/provenance fields.
17. Universe prior uses only point-in-time-safe events.
18. Resolver returns deterministic pair selection under ties.
19. Historical headline can render without spot while spot-dependent panels remain absent.

### Frontend cases

Cover at least:

- Upcoming published event never renders an expected-move dash when payload satisfies the new contract.
- ML, validated options, indicative options, historical, and historical-prior methods receive distinct semantic styles without changing column width.
- Hover explicitly shows `ML unavailable` for non-ML methods.
- Historical hover shows `Market implied unavailable`.
- Ticker page renders headline forecast without strict options evidence or spot.
- ML-only controls/quantiles do not appear as if valid under historical fallback.
- Options panels do not render fabricated data.
- Screener/watchlist show the same display forecast as the calendar.

### Contract tests

Validate enum values, finite positive upcoming display percentages, method/status consistency, backward compatibility of existing `em_*` fields, and public JSON serialization.

## Documentation updates

Update at least:

- `docs/PUBLIC_DATA_CONTRACTS.md`
- `docs/NUMBER_TO_UI.md`
- relevant reconciliation/control-plane documentation

Public contract wording must make clear:

> `display_forecast_pct` is Quantiv's best currently available presentation estimate according to a documented hierarchy. `em_ml_pct` represents only an actual ML forecast and is never populated by fallback methods.

## Migration and compatibility

This is an additive public-contract change.

Existing `em_ml_pct`, `em_straddle_pct`, `em_iv_pct`, quantiles, and detailed options fields retain their current meaning.

Frontend consumers migrate from locally deriving a headline expected move to using `display_forecast_pct` and `display_forecast_method`.

No fallback value may be written into legacy ML-specific fields.

## Acceptance criteria

The change is complete when all of the following hold:

- Every upcoming event in `calendar-reference.json` has a finite positive display forecast in the generated week payload.
- Homepage expected-move dashes are eliminated for upcoming published events.
- ML rows remain visually primary without adding badges or extra horizontal clutter.
- Non-ML hover states explicitly say `ML unavailable`.
- PAYX-like marginal quote failures surface a current indicative options estimate rather than a dash.
- FUL-like unusable chains do not produce garbage options estimates and fall back to history.
- Tickers with inadequate history use a versioned universe prior.
- Strict ML/options eligibility metrics remain unchanged by display fallbacks.
- Display method mix and coverage are observable in build/control outputs.
- Calendar, screener, watchlist, and symbol page agree on the canonical display forecast.
- No post-event information leaks into pre-event historical forecasts.
- Existing ML validation, model control, and decision-quality behavior remains fail-closed.
