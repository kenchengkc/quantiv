# Historical out-of-sample model admission

The approved objective is to replace the mandatory wait for 200 new future events with historical out-of-sample qualification, run the real evaluation, activate only passing forecasts, and merge after CI passes. Freshness, source integrity, native inference, monitoring, rollback, and ML-unavailable behavior remain required.

## Evidence

Use a fresh candidate trained before a sealed historical test period. Preserve the full source training tables, reserve one common 180-day calendar window across all requested leads in a separate workspace, and purge every development table against the earliest reserved snapshot across the entire candidate. Hyperparameter tuning, early stopping, quantile fitting and calibration use development rows only. The frozen candidate is scored across three chronological test blocks; each requires at least 30 matched observations. This is an exact-artifact fixed-origin backtest, not an expanding-window procedure claim.

Evaluate the point head and all five quantile heads together. Each lead/cohort needs 200 distinct earnings events and matched baseline observations, baseline MAE improvement, interval and individual quantile coverage within 0.08, and acceptable temporal-block stability. Paired uncertainty is clustered separately by issuer and earnings week; use conservative multiplicity-adjusted confidence bounds across declared cells. Historical medians are evaluation benchmarks only.

Sign retained inputs, predictions, policy, split/exposure metadata, source model manifest and validation receipt identities. Disclose that historical model-family exposure and historical calendar vintages are not globally established; do not describe current-run exclusion as proof that no researcher previously saw those outcomes. Reject unsafe feature or label timing. No retrospective evidence enters the prospective prediction ledger.

Persist a signed reservation in the durable event registry before fitting. Persist the exact native candidate claim before reading reserved outcomes. Event claims span all leads, so changing directories, packaging timestamps, selected heads or model weights cannot manufacture another independent test from consumed events.

## Partial serving

The tested source bundle remains immutable. Derive a signed serving bundle containing only passing lead heads, with identical native and metadata artifacts. Its authenticated capability map restricts cohorts per head. Embed the signed source manifest and admission receipt so verification does not depend on mutable external claims. Legacy bundle identities and rollback behavior remain compatible.

Every batch, backend, forecast validation and publication path enforces exact declared heads and admitted cohorts. Unsupported combinations remain unavailable. A newer refit cannot inherit exact-artifact admission.

## Operations

Stage genuine provider-backed corporate-action coverage and verified data releases before broad historical evaluation. Historical training admission never grants current production activation. Preserve current-session price, corporate-action, event identity, options eligibility where applicable, drift, prediction replay, exact-bundle activation and import ordering checks. Retain prior champion for rollback and persist the prospective monitor.

Quarantine issuers with invalid provider action records using retained raw query evidence and exhaustive pagination proofs. Keep them visible as unavailable. Enforce authenticated action coverage in scoring and monitoring, including legacy rollback artifacts.

The API verifies the current signed monitor and data-release identity before using a cache or database snapshot. Signed eligibility binds the exact canonical feature vector, spot, scoring timestamp, model, event, snapshot and lead. A same-day source correction or failed database import therefore revokes older inputs. Rollback refreshes monitoring after exact activation/import when current source checks pass.

Run backtests before promotion, archive evidence regardless of outcome, and publish only when both historical qualification and current operational gates pass. If data remain stale or every cell fails, report that result explicitly and preserve the serving hold.

The manual historical-admission workflow separates source preparation, fitting and activation. Long fitting does not hold the daily-refresh execution lock. An exact retained qualified bundle can retry activation without refitting or consuming its historical test again.
