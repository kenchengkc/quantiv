# Independent model evaluation

Quantiv's weekly model-selection path now separates current-candidate development validation from a post-selection chronological final test. The guarantee is deliberately scoped to the current retrain: pre-protocol/model-family exposure to the same historical labels is not asserted.

## Execution order

1. Feature engineering writes the complete historical training tables.
2. The existing realized champion-outcome/rollback control may run on the complete history and the production prediction ledger. This operational safety control remains ahead of challenger selection.
3. The complete feature-engineering tables pass the normal training-artifact validation gate.
4. `scripts/research/independent_model_evaluation.py prepare` considers the latest 120 completed calendar days for the final test.
5. Before reserving rows, it reads the exact prediction ledger used by the operational outcome path. Every `(symbol, earnings_date, horizon)` key present in that ledger is excluded from the final test. The reservation records the ledger path, SHA-256 digest, row count and unique join-key count.
6. The reservation also removes an embargo immediately before the test window. The embargo is the larger of the configured purge and target-availability windows. The current target can use the first post-earnings observation up to five calendar days after the event, so the default effective embargo is five days.
7. The script physically rewrites `data/ml_training` to the development rows only. Operationally excluded recent rows, embargo rows and final-test rows are all absent from the corpus used for challenger selection.
8. The reduced development tables pass training validation again.
9. Optuna tuning, LightGBM early stopping, quantile fitting, purged walk-forward checks and the common-holdout candidate/champion comparison run on the reduced development corpus. Drift and shadow checks use forward-looking candidate forecasts rather than final-test labels.
10. `model_control_plane.py decide` records the promotion decision before the independent evaluator executes.
11. Only when that decision has already promoted the challenger does `independent_model_evaluation.py evaluate` score the promoted signed bundle on the sealed rows.
12. Evaluation performance is retained as evidence and is not a post-decision performance gate. A poor result is not fed back into that run's promotion decision.

## What the independence claim means

For a successful evaluation receipt, the repository establishes all of the following for **that current candidate-selection run**:

- reserved test labels were absent from hyperparameter tuning;
- reserved test labels were absent from point-model and quantile-model early stopping/calibration;
- reserved test rows were absent from the candidate's development-validation/common-holdout corpus;
- reserved test rows were disjoint from the exact prediction-ledger keys that could have supplied realized labels to the operational outcome/rollback calculation earlier in the same workflow;
- final-test performance was computed after the promotion decision and was not used as a post-decision gate.

The receipt also states `historical_model_family_exposure_status=not_established`. That is intentional. Models, researchers or controls before this protocol may have observed some of the same historical outcomes. This mechanism must therefore not be described as proof that the entire model family has never seen the test history.

Repeated future research on already disclosed final-test results is a model-governance concern even when the workflow never feeds those numbers back automatically. Material changes to the target, feature family, tuning search space or candidate family should use a new recorded model-selection family or a newly accrued forward evaluation period rather than silently treating old disclosed outcomes as pristine evidence.

## Retained evidence

A successful promoted-model evaluation is written under:

`data/models/evaluations/<bundle_id>/`

The release contains the exact final-test feature/target rows, per-event predictions and `receipt.json`. The signed receipt binds:

- the promoted immutable bundle ID;
- the model-validation receipt ID and development training-bundle digest;
- the source revision and test-reservation ID;
- the operational prediction-ledger exclusion evidence;
- the model-selection family and current-run non-use declarations above;
- per-horizon paired model/baseline results;
- issuer-clustered and earnings-week-clustered bootstrap intervals;
- a conservative interval envelope rather than an aggregate significance claim;
- a content-addressed research manifest covering the decision record, signed model bundle, retained test inputs, generated predictions and the prediction ledger when present.

The evaluation receipt is Ed25519-signed with the same workflow-held signing key used by the model control plane. Verification re-checks the receipt signature, signed model bundle, model-validation identity and research manifest. If a prediction ledger was present when the reservation was created, evaluation also requires those ledger bytes to still match the recorded digest.

## Development validation versus final test

Existing `val_*` metrics in model metadata remain development-validation metrics. They are allowed to drive early stopping, tuning diagnostics, calibration checks and promotion gates. They must not be described as independent final-test performance.

The independent final test remains external to model metadata because model metadata is packaged before the promotion decision. The public validation surface should eventually show development validation and current-run independent evidence as separate classes. Legacy champions trained before this protocol cannot be retroactively labeled as having this current-run independent evidence.

## Reproducibility and uncertainty

The retained release can replay scoring using the exact signed model bundle and exact retained final-test rows. The receipt records bootstrap seed and draw count. Paired model-versus-straddle error differences are bootstrapped by issuer and separately by earnings week; the reported conservative interval spans both intervals. This is designed to avoid pretending row observations are fully independent when issuer/time dependence exists.

Row-observation totals across horizons are not treated as unique events. The receipt separately reports the union of event identities across horizons and explicitly sets `aggregate_significance_claim=false`.
