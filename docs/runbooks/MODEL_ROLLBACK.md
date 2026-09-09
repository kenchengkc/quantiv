# Model Rollback and Serving Recovery

Use this runbook when a promoted champion must be reverted, Railway is serving an unexpected bundle, signed-model verification fails, or realized-outcome controls trigger an automatic rollback.

The production identity is the signed champion/control-plane state plus the serving activation receipt—not whichever model files happen to exist on disk.

## Trigger symptoms

- model control decision reports rollback/promotion inconsistency;
- serving activation receipt is absent/failed after a promotion;
- Railway reports a different bundle ID from the expected champion;
- R2 model manifest/signature/digest verification fails;
- forecast import references a bundle different from the activated champion;
- realized champion outcomes cross the configured rollback boundary.

## Establish current state

Record:

- signed current champion pointer and bundle ID;
- previous approved bundle ID / registry state;
- `model_decision.json` and `model_outcomes.json` from the relevant retrain;
- serving activation receipt;
- exact production forecast path and its `model_bundle_id`;
- R2 model pointer/manifest identity;
- Railway deployment revision and health response.

If any identities disagree, do not choose the newest one by timestamp. Determine which state completed the signed promotion/activation contract.

## Preferred rollback path

Use the repository's isolated `model-rollback.yml` provenance-recovery workflow for an operator-selected previously approved bundle. Do not manually replace model files on Railway or mutate a historical bundle in R2.

The rollback must:

1. select an exact previously approved target bundle;
2. verify the signed registry/control provenance for that target;
3. verify the bundle manifest/signature and every artifact digest;
4. update the control-plane champion through the supported rollback primitive;
5. activate that exact bundle on the serving backend;
6. rescore/revalidate production forecasts if the production forecast must match the restored champion;
7. import only the exact validated rollback forecast into Neon when applicable;
8. preserve decision/activation/import receipts.

## Automatic rollback during retrain

When `model-retrain.yml` evaluates realized champion outcomes and automatically rolls back:

- verify `model_outcomes.json` explains the threshold/control that fired;
- verify the subsequent `model_decision.json` identifies the expected restored champion;
- if no new challenger is promoted, ensure the production forecast is explicitly rescored with the rollback bundle;
- verify Railway activation and Neon import refer to that same bundle ID.

A successful control-plane rollback with an old forecast still referencing the rejected champion is not a complete recovery.

## Signature/digest failure

Treat a signature or digest mismatch as an integrity incident.

- do not regenerate the signature around the suspect bytes;
- do not overwrite the existing immutable bundle key;
- preserve the failed manifest/bundle for investigation when safe;
- restore a previously approved verified bundle through the rollback workflow;
- investigate publication/credential compromise before promoting another candidate.

## Railway activation failure

If the control plane promoted a candidate but serving activation failed:

1. keep the candidate/control decision evidence;
2. do not import candidate forecasts as if the serving backend had activated them;
3. inspect backend health, R2 access, admin authentication, volume/model filesystem state, and activation response;
4. retry activation only for the exact expected bundle ID;
5. if activation cannot be restored promptly, roll the champion/control plane back to the prior approved bundle and verify the old serving state.

## Verification

Recovery is complete only when:

- signed champion pointer resolves to the intended bundle;
- the target bundle passes manifest/signature/digest/native-load verification;
- Railway reports/serves that exact bundle;
- the production forecast references that exact bundle;
- Neon import, if used, references that exact bundle and an activation receipt;
- validation/control receipts pass;
- production smoke and health checks pass.

## Evidence to retain

Keep the old/new champion IDs, registry/control objects, decision/outcome reports, bundle manifests/signatures, activation/import receipts, workflow logs, and exact rollback target. Open a follow-up issue if any identity transition required a manual bypass.
