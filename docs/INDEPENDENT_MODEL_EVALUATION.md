# Independent model evaluation

Quantiv's weekly model-selection path has a separate final-test boundary from development validation.

## Execution order

1. Feature engineering writes the complete historical training tables.
2. The complete tables pass the normal training-artifact validation gate.
3. `scripts/research/independent_model_evaluation.py prepare` reserves the latest 120 completed calendar days as final-test rows. It removes those rows from `data/ml_training` and also removes an embargo immediately before the test boundary.
4. The embargo is the larger of the configured purge and the target-availability window. The current target can use the first post-earnings observation up to five calendar days after the earnings date, so the default effective embargo is five days.
5. The reduced development tables pass training validation again.
6. Optuna tuning, LightGBM early stopping, quantile fitting, purged walk-forward checks, common-holdout champion comparison, drift checks, and shadow scoring run only on the reduced development corpus or forward-looking forecast inputs.
7. `model_control_plane.py decide` records the promotion decision without access to the reserved final-test files.
8. Only when that decision has already promoted the challenger does `independent_model_evaluation.py evaluate` score the promoted signed bundle on the sealed rows.
9. Evaluation performance is reported but is not a post-decision performance gate. This prevents repeated test-set reuse from silently becoming another tuning loop.

## Retained evidence

A successful promoted-model evaluation is written under:

`data/models/evaluations/<bundle_id>/`

The release contains the exact final-test feature/target rows, per-event predictions, and `receipt.json`. The signed receipt binds:

- the promoted immutable bundle ID;
- the model-validation receipt ID and development training-bundle digest;
- the source revision and test-reservation ID;
- the model-selection family and explicit declarations that test labels were not used for tuning, stopping, calibration, or the promotion decision;
- per-horizon paired model/baseline results;
- issuer-clustered and earnings-week-clustered bootstrap intervals;
- a conservative interval envelope rather than an aggregate significance claim;
- a content-addressed research manifest covering the decision record, signed bundle, retained final-test inputs, and generated predictions.

The model evaluation receipt is Ed25519-signed with the same workflow-held signing key used by the model control plane. Verification also re-verifies the signed model bundle and the research manifest.

## Development validation versus final test

Existing `val_*` metrics in model metadata remain development-validation metrics. They are allowed to drive early stopping, tuning diagnostics, calibration checks, and promotion gates. They must not be described as the independent final test.

The independent final test is deliberately external to model metadata because model metadata is packaged before the promotion decision. A future public validation projection should show development validation and independent test results as separate evidence classes. Legacy champions trained before this protocol cannot be retroactively labeled independently tested.

## Reproducibility boundary

The retained release is sufficient to replay the independent scoring calculation using the exact signed model bundle and exact final-test rows. The receipt records the bootstrap seed and draw count. Row-observation totals across horizons are not treated as unique events; the receipt separately reports the union of event identities across horizons.

This protocol improves independence of the reported final test. It does not by itself make repeated future model-family experimentation free of researcher degrees of freedom. Material changes to the feature family, target, tuning search space, or candidate family should be recorded as a new model-selection family rather than silently reusing the same label.
