# Model learning architecture repair

## Problem

The weekly pipeline confuses permission to learn from verified historical data
with permission to activate a production model. It reserves the newest 120 days,
then saves selection models fitted on only the oldest 75% of the remainder.
Historical targets and some features also violate the forecast-time contract;
the common holdout can contain labels previously seen by the champion.

## Contracts

1. Verified immutable historical sources and a validated, mature training cohort
   admit training. A held current options candidate must not prevent historical
   fitting, signed candidate retention, or realized-outcome reporting. Current
   decision-safe reconciliation, freshness, forecast quality and promotion checks
   remain mandatory before production changes.
2. New training uses an explicit causal feature/target protocol. BMO reactions
   run from the previous close to the report-day close; AMC reactions run from
   the report-day close to the next close. Unknown timing is explicit and is not
   represented as a verified reaction. Corporate action normalization applies
   across the price pair. Each row retains snapshot and label-availability dates.
3. Historical features use only observations available by the prediction
   snapshot. VIX percentiles rank a trailing chronological window. Existing
   bundles without protocol metadata retain their explicit legacy construction;
   unknown protocols fail closed.
4. Selection splits and expanding walk-forward folds purge labels according to
   the first validation prediction snapshot, not just its earnings date. Fold
   recipes do not consume future validation tuning results. Time decay uses
   the stated half-life.
5. After selection, freeze each head's parameters and tree count, then refit on
   all eligible mature development rows. Retain actual fitting dates, row digest,
   protocol and selection exposure separately from selection metrics. The saved
   refit is not described as independently tested on labels it consumed.
6. Historical training includes the same supported optionless cohort as live
   scoring, with separate cohort evidence. Unsupported cohorts are withheld.
   Baselines and drift references must match the cohort being assessed.
7. Promotion requires genuinely unseen paired evidence for both models. Legacy
   date exposure is conservatively excluded when exact historical identities are
   unavailable. A final refit cannot reuse its own selection labels as independent
   evidence. Retain a frozen challenger for prospective paired evaluation when
   retrospective unseen rows are insufficient; weekly candidates must not erase
   that challenger's accumulating evidence. Every model receives vectors built
   under its own protocol.
8. Archive a verified signed candidate before live-data activation gates. Empty
   upcoming events or a prospective data hold retain the candidate without
   mutating the champion. Immutable candidate storage must not sync unrelated
   mutable model/data pointers.
9. All reports distinguish fitting, selection, independent/prospective evidence,
   challenger retention, activation and production publication. A successful
   training run alone never implies a promoted model.

## Scope and validation

Repair feature construction, training/refitting, evaluation, drift, historical
admission, candidate persistence, and weekly workflow orchestration together.
Preserve signed bundles and receipts, atomic pointers, provider budgets, the
65% options coverage gate, minimum evidence, calibration and regression limits.
No market-data provider calls are needed for implementation or tests.

Regression fixtures cover session boundaries, future-data invariance, all long
horizons, recent final fitting, protocol incompatibility, exposed-row exclusion,
cohort matching, held-live-data training, candidate retention and unchanged
production pointers. Run repository-required Python checks and workflow lint;
obtain independent review before integration.
