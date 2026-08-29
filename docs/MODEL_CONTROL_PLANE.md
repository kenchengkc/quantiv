# Model control plane

Quantiv's model control plane is a fail-closed, file-backed promotion system.
It adds no Redis keys, Neon tables, queue, or always-on service. Immutable
bundles, compact reports, and a prediction ledger use the existing R2 sync.

## Artifact trust boundary

Production estimators use LightGBM's native text format, which cannot execute
arbitrary Python objects when loaded. The retraining workflow holds an Ed25519
private key; serving images contain only the pinned public key.

Each immutable `models/bundles/<sha256>/` directory contains:

- one point model and five quantile heads for every supported horizon;
- the exact model metadata and ordered feature schema;
- a signed manifest with every filename, byte length, SHA-256 digest, source
  revision, and passing validation receipt ID.

R2 uploads immutable bundle contents first and replaces the signed champion
pointer last. Railway synchronization verifies that pointer and manifest,
downloads only declared filenames into a temporary directory, verifies every
digest, and native-loads all six point models and thirty quantile heads before
atomically changing the `current` symlink. Interrupted, partial, unsigned,
replayed, altered, or unloadable bundles leave the previous champion active.

## Training and challenger gates

A challenger cannot reach the promotion decision unless all of these pass:

1. feature and target integrity, chronology, duplicate, history, and purge
   checks;
2. six point models and thirty quantile heads load from native format and
   smoke-predict against their exact schema;
3. the chronological holdout beats the straddle baseline and passes point,
   interval, quantile, crossing, and nonnegative-output gates;
4. four expanding 60-day walk-forward windows use a five-day purge, beat the
   straddle baseline in aggregate and in at least half the folds, and avoid a
   catastrophic worst-fold regression;
5. the candidate forecast passes the end-to-end feature, IV, quantile, and
   arithmetic handoff checks.

When a champion already exists, both bundles are then scored on the candidate's
same purged holdout rows. Promotion permits no more than 2% MAE regression and
no material calibration regression. Upcoming-event shadow scoring detects
large output divergence before the signed pointer changes. A valid challenger
that does not win remains in the signed registry; it does not replace production
forecasts or serving models.

The first complete gated cycle ran on 2026-08-23. All training, four-fold
purged walk-forward, artifact, shadow, and handoff validations reached the
promotion decision. The challenger was retained rather than promoted because
its T-1 and T-3 calibration regressed on the common holdout. This is the
expected fail-closed behavior: completing a retrain is not evidence that the
new bundle deserves production traffic.

## Production monitoring and rollback

Every daily forecast run records the champion plus available challenger and
previous-champion shadow predictions in a compact two-year Parquet ledger.
The ledger and current monitoring report have a signed digest receipt.

Monitoring reports:

- feature PSI and missingness changes against per-horizon training references;
- candidate/champion forecast divergence;
- realized MAE against both the options/straddle baseline and the comparison
  bundle;
- residual mean and variance against validation references;
- P10/P25/P50/P75/P90 and 50%/80% calibration;
- calibration slices by broad sector, VIX regime, equity-dollar-volume
  liquidity cohort, and DTE.

Automatic rollback is deliberately conservative. It requires at least 30
matched realized events scored by both bundles, a comparison bundle with at
least 5% lower MAE, and either champion MAE worse than the straddle baseline by
more than 5% or severe 80% interval undercoverage. Rollback creates a new signed
pointer with the evidence embedded in its decision record; it never mutates an
old bundle.

## Serving handoff

A promotion or rollback is incomplete until serving activation and forecast
import prove the same 64-character bundle ID:

```text
signed control decision
→ immutable bundle upload
→ champion pointer promoted last
→ Railway receives expected bundle ID
→ digest + native-model preflight
→ atomic serving-path activation
→ serving activation receipt
→ same-bundle forecast import to Neon
```

`ADMIN_API_KEY` and `DATABASE_URL` are mandatory for this handoff. The workflow
fails visibly instead of treating either missing secret as a successful skip.
`scripts/activate_model_bundle.py` writes a content-addressed
`quantiv.serving-activation.v1` receipt. The Neon importer then rejects mixed
bundles or a forecast whose `model_bundle_id` differs from the activated
champion. Rollback rescoring explicitly reads the signed rollback bundle rather
than the newly trained candidate directory.

A rejected challenger has exercised the complete pre-promotion path. The first
genuinely superior challenger must still exercise production activation,
same-bundle Neon import, and subsequent rollback-capable monitoring. Until that
occurs, those operational steps are implemented and tested but are not claimed
as production-proven.

The import step now consumes the serving activation receipt rather than only a
shell variable. After the Neon transaction commits, it emits
`quantiv.forecast-import.v1` with the activated bundle ID, activation receipt
ID, forecast digest, row counts, feature-vector coverage, and horizons. A
promotion or rollback therefore leaves an artifact chain proving the same
bundle reached both serving and stored forecasts; a different ID fails before
the database connection is opened.

## Main files

- `apps/ml/ml/model_bundle.py`: signing, verification, manifests, pointers, and registry.
- `apps/backend/services/r2_models.py`: verified atomic R2 activation.
- `apps/ml/ml/walk_forward_validation.py`: mandatory walk-forward gate.
- `apps/ml/ml/model_control.py`: common-holdout comparison, drift, shadows, outcomes.
- `scripts/model_control_plane.py`: workflow-facing decisions, monitoring, and rollback.
- `scripts/activate_model_bundle.py`: exact-bundle serving activation and receipt.
