# Model fitting and independent evaluation

Scheduled retraining uses the full eligible mature historical cohort. Chronological
selection and causal expanding walk-forward checks select recipes, then every head
is refitted on all mature development rows. Metadata records fitting dates, row
digests, label exposure and selected/actual tree counts.
Default causal extraction caps maturity at the latest completed US market
session, including early closes, so an intraday manual run cannot train on a
partial current-day close. Explicit historical replay cutoffs remain available.

Selection metrics are marked `selection_only`. They are not independent accuracy
claims for the saved refit, which consumes those labels. Weekly `report-learning`
evidence separates fitting, retention and activation, explicitly recording
`consumed_labels_are_independent=false`.

Successful fitting uploads research trees, metadata and exact fitting tables even
if a later model-quality gate fails. Those diagnostics do not certify promotion;
only fully validated candidates receive a signed production bundle and registry
designation.

## Prospective promotion evidence

Signed candidates and provenance are retained before live-data checks. The registry
freezes one compatible challenger while weekly learning continues. Daily monitoring
builds each bundle's inputs under its own protocol and records immutable first
predictions with observation and recording timestamps.

Promotion pairs the same symbol, event, horizon and snapshot against the common
corrected BMO/AMC session-reaction target. Both bundles' fitting and selection
exposure must precede that prediction snapshot. Backfilled forecasts cannot count
as prospective observations. Labels must be mature and versioned.

Minimum evidence remains 200 paired observations per horizon and supported serving
cohort. Options rows use strict straddle baselines; optionless rows use historical
medians available at prediction time. MAE, calibration, forecast handoff and current
data activation gates remain mandatory. Insufficient evidence retains the challenger
and champion without claiming independent success.

The existing promotion limits are safety and non-regression checks: a candidate can
pass with up to 2% higher MAE than the champion. Passing them does not establish
that the candidate improved accuracy. A claim of improvement needs a separate,
predefined paired analysis with uncertainty; the 200-row floor alone is not proof.

Two experiments answer different questions:

1. To isolate the benefit of additional data, compare earlier and later training
   cutoffs using the same causal features, labels, recipe and tuning procedure.
   Evaluate both on the same later unused earnings windows, repeating the exercise
   chronologically. Changing targets, features or fitting rules in only one arm
   would confound the effect of the new data.
2. To assess the actual retained bundles, collect their paired predictions before
   announcements, then wait for mature common labels. Choose the primary metric,
   minimum meaningful improvement, supported cohorts and decision checkpoint before
   inspecting outcomes. Report paired MAE differences and cluster uncertainty by
   issuer or earnings event instead of treating related rows as independent.

The research paired-benchmark CLI supports reproducible cluster bootstrap intervals
via `--cluster-column`; it does not enforce an automatic superiority gate or adjust
for repeated looks across cohorts and horizons. Those choices must be specified in
the experiment design. Historical selection results and a positive pooled average
cannot substitute for this evidence. Even strong evidence describes the tested
population and period, not a guarantee for every future market regime.

## Monitoring collection and publication holds

Daily and nightly event-freeze monitoring persist the signed prediction ledger,
report and receipt independently of public ML publication. The monitoring-only R2
writer verifies a staged copy of all three files before any upload and writes the
receipt last. Model and forecast upload modes do not own these prediction files;
the retrainer's separate outcome evidence remains separately owned.

Daily refresh and nightly freeze share the same execution concurrency group for
the complete pull, append and upload sequence. This prevents two valid cumulative
ledgers from replacing one another with missing observations. Readers still verify
the receipt and its bound bytes and reject incomplete uploads. These persistence
repairs do not make stale inputs eligible: prospective evidence must independently
pass snapshot freshness, announcement timing and model exposure checks. Publication
and model activation retain all existing quality gates.

## Retained provenance

`data/models/candidates/<bundle_id>/` retains original training tables, temporal and
statistical receipts, model-validation receipt, historical admission, calendar inputs
and feature sources. A signed inventory binds every member. R2 copies immutable
objects and verifies them before registry updates. A frozen candidate uses its own
archived evidence, not another weekly run's training files.

Archive preparation checks those relationships before signing: training and model
bytes must match the bundle's model-validation receipt, calendar bytes must match
historical admission, and normalization sources must match temporal receipts.
Historical labels require verified corporate-action coverage for their symbols
and exact reaction boundaries; missing controls cannot imply no corporate actions.

The monitoring prediction ledger and reports have signed digest receipts. A successful
training workflow establishes fitting and retention. Production replacement requires
signed activation and serving/import receipts for the exact bundle.

Model validation and walk-forward checks assess every supported serving cohort
separately with the existing sample, baseline, calibration and raw-quantile limits.
Pooled results for withheld cohorts remain diagnostics. Optionless forecasts must
omit quote-dependent fields; quote-backed forecasts retain all spread and quality
checks. Public outcomes use the same adjusted session target. Unverified external
close fallback stays outside canonical outcomes.

## Explicit historical research mode

The research CLI retains `prepare` and `evaluate` for explicitly sealed historical
tests, outside scheduled training. Embargoes use actual label availability before
the first prediction snapshot, or horizon plus conservative label lag for legacy
rows. Evaluation rejects models whose fitting or selection exposure overlaps the
test, and can assess retained signed candidates without pretending they were promoted.

Historical receipts scope independence to the recorded selection run. They do not
establish that earlier researchers or model families never saw those labels. Repeated
disclosed outcomes cannot be treated as untouched evidence. Retained evaluation
includes exact rows and predictions, signed bundle identity, clustered uncertainty
and a content-addressed research manifest.
