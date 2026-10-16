# Upcoming ML forecast coverage

Upcoming, unreported events display an eligible signed ML forecast or **ML unavailable**. Historical move statistics remain useful context and evaluation benchmarks; they do not supply the headline forecast. Previously reported forecasts retain their original publication history.

The calendar export covers the next 21 calendar days, including the fourth upcoming week and weekend endpoints. New training uses exact daily lead heads T1 through T25: the latest completed market session can precede today by four calendar days under the verified session calendar. Consumers load the exact horizon set from the signed manifest, so older six-head bundles remain usable for rollback. Missing heads never select a nearby model or interpolate predictions.

Live scoring selects the newest available verified price session from the latest completed session and its immediate trading-session predecessor. The one-session grace counts exchange sessions, including holidays and early closes. Every feature remains anchored to that actual snapshot, and the model lead is earnings date minus snapshot date. The date is preserved in forecast artifacts, signed monitoring, API responses and the public forecast's as-of field. Two-session-old inputs, unfinished-session inputs and events past their prediction deadline remain held. Grace never substitutes a nearby model when the exact lead is outside the signed bundle.

## Verified optionless inputs and outcomes

Corporate-action ingestion v2 includes the options universe, price-backed historical earnings, upcoming earnings, and forecast-ledger events. Exact source snapshots and symbol membership are retained in the immutable data release, with exhaustive split/dividend query receipts. Historical retired symbols stop at their recorded retirement boundaries. Existing v1 receipts retain their original, narrower coverage; upgrading code alone does not expand a receipt's verified symbols.

Promotion outcomes are collected directly from authenticated prediction identities and current canonical earnings timing. They require the exact reaction-session closes, verified corporate-action coverage, and a mature label. An absent options chain or training feature row no longer removes an otherwise verifiable outcome. Rescheduled dates or conflicting timings cannot establish a label for the original prediction.

## Independent admission

Registration declares an evaluation contract inside the signed model registry before admissible predictions are recorded. The contract binds candidate, incumbent, exact heads, supported cohorts, policy hash, and start time. Weekly refits do not replace that frozen challenger. Expanding a six-head challenger to daily coverage starts a new evaluation while preserving the earlier immutable evidence.

Prospective admission requires at least 200 independent future events and 200 matched baseline observations per head and cohort. Historical admission can instead qualify the exact frozen candidate using reserved historical events excluded from that candidate's fitting and selection; see [the historical admission protocol](superpowers/specs/2026-10-15-historical-ml-admission.md). Existing incumbent-supported coverage retains its paired incumbent regression checks. Newly supported coverage must beat the matched straddle or historical-median benchmark and satisfy each interval/quantile calibration target within 0.08. Predictions and quantiles must remain finite, nonnegative and ordered. Labels consumed by fitting or selection, predictions recorded before the declared prospective evaluation, snapshots outside the one-session grace, and late predictions cannot count.

The benchmark is only an admission comparison. It is never substituted for the model's public prediction. Insufficient evidence retains the incumbent and leaves unsupported public rows unavailable.

## Rollout

1. Deploy the code and run the normal data refresh to create a genuine v2 corporate-action receipt and verified release. Options older than the one-session grace remain held; strict forecasts require verified options and prices from the same actual snapshot date.
2. Run the model retrain workflow against that release. It trains, validates and archives the daily heads, registers the frozen challenger, and records the admission contract before shadow scoring. Its 360-minute limit accommodates the larger training grid.
3. Allow scheduled monitoring to retain prospective predictions. Subsequent retrains collect verified outcomes independently of training tables and evaluate the same frozen challenger.
4. Promotion occurs only after independent admission, current input checks, forecast validation and drift checks pass. The exact promoted bundle is activated before its forecast is imported. No forced promotion or historical fallback is part of this rollout.

Coverage must be measured against the complete calendar ticker/date denominator. Missing timing, current prices, validated heads or sufficient model evidence remains an explicit exclusion, rather than a fabricated forecast. Code tests alone do not establish production model coverage.
