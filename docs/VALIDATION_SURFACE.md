# Public research validation surface

`/validation` is Quantiv's public due-diligence view for researchers and engineers. It answers a narrower question than the protected `/ml-status` operations page: **does the research model add information, is its uncertainty calibrated, and what evidence currently supports publication?**

## Public artifact

The page reads `apps/frontend/public/evidence/model-validation.json`, schema `quantiv.public-model-validation.v1`.

`tools/build_public_validation.py` generates the artifact from model metadata already present in the runner. After the nightly R2 pull it prefers the active model bundle named by `data/models/control/champion.json`; local and preview builds fall back to the checked-in `apps/ml/models/metadata_T*.json` files.

The projection publishes only compact due-diligence fields:

- horizon-specific train/validation row counts;
- validation MAE/RMSE/R² and the same-row market-straddle MAE baseline;
- relative MAE improvement versus the straddle baseline;
- P10/P25/P50/P75/P90 empirical coverage;
- 50% and 80% interval coverage and average widths;
- model version, training timestamp, feature count, and quantile heads;
- validation protocol and decision-scope declarations;
- current forecast receipt and control-plane status;
- active bundle or model-artifact identity when available.

It deliberately does **not** publish filesystem paths, feature vectors, tuning parameters, credentials, or administrative controls.

## Nightly publication

`tools/build_control_plane_snapshot.py` writes `control-plane.json` first, then regenerates the public validation artifact in the same step. This keeps the page aligned with the exact data/model/release state committed by the nightly workflow and avoids a second hand-maintained status path.

The builder fails closed if any supported horizon lacks required validation metadata. A preview/local environment may display `baked_fallback` as the model source; the hosted nightly publication should switch to `signed_champion` whenever the pulled champion bundle is present.

## Interpretation

The headline comparison is predictive validation, not trading P&L. Quantiv predicts absolute earnings-move magnitude and compares its error with the market straddle expected move on the same validation observations.

The page preserves the existing decision boundary:

- decision scope: `end_of_day_research`;
- live trading eligible: `false`;
- current stock price may update spot-derived inputs only;
- options, IV, Greeks, and other market features remain tied to their validated snapshot.

`degraded` is intentionally different from `failed`. Advisory coverage or drift warnings remain visible when publication is still decision-safe; critical controls fail publication closed.

## Verification

```bash
python tools/build_public_validation.py
pytest tools/tests/test_build_public_validation.py -q
npm run test:e2e --workspace=apps/frontend -- validation.spec.ts
```

The normal repository CI also runs the broader frontend, Python, generated-data, worker, and container checks.
