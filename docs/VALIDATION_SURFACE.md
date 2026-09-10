# Public research validation surface

`/validation` is Quantiv's public due-diligence view for researchers and engineers. It answers a narrower question than the protected `/ml-status` operations page: **does the research model add information, is its uncertainty calibrated, and what evidence currently supports publication?**

## Public artifact

The page reads `apps/frontend/public/evidence/model-validation.json`, schema `quantiv.public-model-validation.v1`.

`tools/build_public_validation.py` generates the artifact from model metadata already present in the runner. After the nightly R2 pull, the presence of `data/models/control/champion.json` establishes a fail-closed production trust boundary. The builder must verify, in order:

1. the Ed25519 signature and schema of the champion pointer;
2. the selected immutable bundle's signed manifest, bundle identity, exact artifact set, byte sizes, and SHA-256 digests;
3. the immutable model-validation receipt selected by the signed bundle's `receipt_id` (`data/models/receipts/models.<receipt-prefix>.receipt.json`), including its recomputed content identity, `models` scope, and passed quality;
4. equality between the signed manifest's `receipt_id` and that recomputed model-validation receipt ID;
5. equality between every model-validation receipt member and the corresponding artifact authenticated by the signed bundle manifest; and
6. when a passed forecast receipt exposes a model-bundle digest, equality between that digest and the verified champion model bundle used for the evaluation projection.

The mutable `data/models/receipts/latest_models.json` pointer is deliberately **not** used to establish active-champion evaluation identity. A later challenger can legitimately advance that pointer without being promoted; the champion's signed manifest remains the authority for which immutable validation receipt belongs to the active bundle.

If a champion pointer exists and any of those checks fail, public validation generation fails. It does **not** silently publish checked-in fallback metrics. Local/preview environments may use `baked_fallback` only when no production champion pointer is present, and that path is explicitly marked `preview_unverified`.

For a verified champion, the builder derives the holdout dates, row counts, purge, walk-forward method, requested folds, validation-window length, and walk-forward metrics from the authenticated horizon metadata rather than re-declaring those run-specific values. It emits a deterministic nested `quantiv.model-evaluation-receipt.v1` whose content-addressed ID binds:

- active bundle ID and bundle source revision;
- authenticated model-validation receipt ID;
- training- and model-bundle digests from that receipt;
- signed metadata digests for every supported horizon;
- per-horizon holdout split audits and walk-forward audits; and
- the exact compact validation metrics projected publicly.

The evaluation receipt is a public projection of an already authenticated chain: the signed model manifest authenticates the content-addressed validation receipt ID, and that receipt binds the training/model evidence. It does not introduce a second private signing key or a parallel model-control path.

The projection publishes only compact due-diligence fields:

- horizon-specific train/validation row counts;
- validation MAE/RMSE/R² and the same-row market-straddle MAE baseline;
- relative MAE improvement versus the straddle baseline;
- P10/P25/P50/P75/P90 empirical coverage;
- 50% and 80% interval coverage and average widths;
- model version, training timestamp, feature count, and quantile heads;
- verified holdout and walk-forward protocol evidence when a signed champion is active;
- current forecast receipt and control-plane status; and
- active bundle, model-validation receipt, source-revision, and model-artifact identities when verified.

It deliberately does **not** publish filesystem paths, feature vectors, tuning parameters, credentials, or administrative controls.

## Nightly publication

`tools/build_control_plane_snapshot.py` writes `control-plane.json` first, then regenerates the public validation artifact in the same step. This keeps the page aligned with the exact data/model/release state committed by the nightly workflow and avoids a second hand-maintained status path.

The builder fails closed if any supported horizon lacks required validation metadata. A preview/local environment may display `baked_fallback` as the model source; the hosted nightly publication must verify the active signed champion and its authenticated validation evidence whenever a champion pointer is present.

A verification failure blocks creation of a new frontend publication. It does not mutate the already deployed immutable frontend release, so the last validated release remains the serving fallback under the existing release/materialization controls. This is intentionally different from silently substituting fallback model metrics into a new public validation artifact.

## Interpretation

The headline comparison is predictive development/selection validation, not trading P&L and not yet an independently untouched final-test claim. Quantiv predicts absolute earnings-move magnitude and compares its error with the market straddle expected move on the same validation observations. The public evaluation receipt makes the provenance of those reported metrics auditable; it does not change the statistical independence of the underlying evaluation design.

The page preserves the existing decision boundary:

- decision scope: `end_of_day_research`;
- live trading eligible: `false`;
- current stock price may update spot-derived inputs only;
- options, IV, Greeks, and other market features remain tied to their validated snapshot.

`degraded` is intentionally different from `failed`. Advisory coverage or drift warnings remain visible when publication is still decision-safe; critical controls fail publication closed.

## Verification

```bash
python tools/build_public_validation.py
pytest apps/ml/tests/test_evidence_receipt.py tools/tests/test_build_public_validation.py -q
npm run test:e2e --workspace=apps/frontend -- validation.spec.ts
```

The focused provenance tests cover receipt tampering, invalid signed pointers, altered signed-bundle metadata, model-validation receipt mismatch, mutable latest-candidate movement, forecast/model mismatch, and run-derived protocol projection. The normal repository CI also runs the broader frontend, Python, generated-data, worker, and container checks.
