# Independent IV and ML Coverage Fallback

Date: 2026-09-17
Status: Approved design
Branch: `fix/independent-iv-forecast-fallback`

## Summary

Quantiv currently treats a decision-eligible or relaxed same-strike call/put pair as the practical prerequisite for both option-derived display forecasts and ML scoring coverage. This causes avoidable historical-median fallbacks when useful contract-level IV exists but a same-strike pair is missing or rejected, and it prevents ML scoring rows from existing when strict straddle features are absent.

This change separates those concerns without weakening the strict research contract.

The user-facing hierarchy becomes:

1. Validated ML expected move.
2. Decision-eligible strict straddle expected move.
3. Relaxed same-strike indicative straddle expected move.
4. Independent near-ATM IV expected move from individual contracts.
5. Ticker historical median.
6. Universe historical prior.

The strict analytical fields and strict option-quality views retain their existing meanings. IV-only display estimates do not backfill `em_straddle_pct`, do not make rejected quotes decision-eligible, and do not alter reconciliation coverage.

## Goals

- Use available contract-level implied volatility before falling back to historical medians.
- Allow IV estimation when calls and puts do not share one strike, and allow a single clean near-ATM side when the opposite side is unusable.
- Keep strict straddle and ML research semantics unchanged.
- Remove the inner-join dependency that makes strict straddle availability a prerequisite for creating ML scoring rows.
- Allow the nightly refresh to continue ML/frontend publication against a verified published options fallback when only the options candidate is stale or fails options-specific quality gates.
- Improve ML availability diagnostics without pretending an untrained horizon is a validated model.

## Non-goals

- Treating an IV-only estimate as an executable straddle price.
- Loosening `v_eligible_straddles`, `v_straddle_features`, reconciliation thresholds, or strict options publication quality.
- Training new LightGBM horizons in this change.
- Using a T-k model on a different lead-time feature vector without validation.
- Fabricating bid/ask, Greeks, straddle price, or quantiles for IV-only rows.
- Hiding stale options evidence; IV estimates retain the actual options snapshot `as_of_date`.

## Independent IV estimator

The display resolver must search individual option contracts only after strict and relaxed same-strike straddle selection fail.

For each expiry in ascending order that spans the earnings event and is within the existing display-policy post-event window:

1. Read current snapshot contracts from the same provider-level raw options source used by the indicative straddle resolver.
2. Normalize call/put side labels.
3. Require a finite positive strike and IV, with IV in `(0, 5]`.
4. Require a structurally sane quote when bid/ask are present: nonnegative bid/ask, ask >= bid, positive midpoint, and individual relative spread <= the existing display-policy `max_leg_relative_spread`.
5. Rank calls independently toward delta `+0.5`; rank puts independently toward delta `-0.5`. If a valid delta is unavailable, rank by strike distance to point-in-time EOD spot. If spot is unavailable, use strike proximity to the median strike for that expiry as a deterministic last-resort ATM proxy.
6. Reject a selected side whose ATM metric exceeds the existing display-policy `max_atm_delta_distance`.
7. If both sides survive, compute simple mean ATM IV. If only one side survives, use that side and record that the estimate is single-sided.
8. Compute expected-move percentage as `atm_iv * sqrt(dte / 365)`.
9. Return the first spanning expiry that produces a finite positive estimate.

The resolver records diagnostic details including `estimator = "atm_iv"`, expiry, DTE, selected strikes/IVs, sides used, ATM metrics, and spot/proxy source.

Public behavior remains `display_forecast_method = "options_indicative"` and `options_status = "indicative"` so existing consumers do not require a schema migration. The fallback reason preserves why pair-based options failed (`quote_quality` or `no_same_strike_pair`).

## ML scoring independence

`get_upcoming_features()` must use an OHLCV-backed snapshot spine rather than `v_straddle_features` as the row-generating relation.

The snapshot spine contains each upcoming event and each available ticker OHLCV date that is:

- before the earnings date;
- between 1 and 25 calendar days before the event.

Strict `v_straddle_features` is then LEFT JOINed by `(ticker, snapshot_date)` with the existing earnings-spanning expiry rules. This preserves strict option features when available, but leaves option-derived model features null when unavailable.

Realized-volatility, historical-earnings, macro, volatility-history, timing, calendar, and spot features continue to be joined at the same snapshot date. Spot prefers realized-vol close / snapshot close before any ATM strike proxy.

LightGBM receives `NaN` for unavailable option features rather than losing the entire scoring row. Existing model artifacts, exact trained horizons, and quantile heads remain unchanged.

## Horizon behavior

This change does not score a T-7 model on T-5 inputs. Exact trained-horizon matching remains the validation boundary.

Coverage improves because exact trained-horizon rows can now exist even when strict options were absent on that snapshot date. Existing forecast selection may continue to publish the closest available exact-horizon forecast for the event.

When model metadata is available to the display resolver and no forecast exists, the resolver may classify `ml_status = "unavailable_model"` only when no trained horizon could have produced a pre-cutoff snapshot for the event. Otherwise it reports `unavailable_inputs`. If metadata cannot be read, it fails conservatively to `unavailable_inputs`.

## Options fallback refresh behavior

An options candidate that fails only the existing soft options-specific fallback codes still rolls back to the last published options partition. After that rollback is complete and the published partition is verified as the newest active local partition, ML scoring and frontend publication may continue.

`FinalizationResult.can_score` therefore means "the refresh may safely continue scoring with the active options state", not "the latest candidate passed strict options reconciliation".

The options status artifact must continue to distinguish:

- `state = "accepted"` for a fresh decision-safe candidate;
- `state = "fallback"` for a verified published fallback;
- `state = "blocked"` for unrelated critical failures.

For fallback state, strict options decision safety remains false in policy metadata even though ML/frontend refresh may continue.

## Diagnostics and invariants

- `em_straddle_pct` and strict straddle fields remain populated only from strict option evidence.
- `display_forecast_pct` may use IV-only evidence before history.
- IV-only selection never changes strict event coverage or reconciliation counts.
- A missing same-strike pair is not synonymous with missing implied-volatility evidence.
- ML feature rows are no longer removed solely because `v_straddle_features` has no row.
- Non-options critical reconciliation failures still block refresh.
- Options fallback keeps its true source date so stale market evidence is visible.
- Existing public display method enums remain backward compatible.
