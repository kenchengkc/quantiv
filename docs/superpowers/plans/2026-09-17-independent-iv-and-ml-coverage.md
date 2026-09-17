# Independent IV and ML Coverage Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for implementation and superpowers:verification-before-completion before claiming completion.

**Goal:** Use contract-level IV before historical fallbacks, make ML scoring rows independent of strict straddle eligibility, validate ML-only forecasts safely, and keep nightly publication moving when a rejected options candidate is replaced by a verified published fallback.

**Architecture:** Strict option views, quote-quality thresholds, and analytical `em_*` semantics stay unchanged. Display-only IV is estimated directly from individual contracts. Live ML rows are generated from an OHLCV snapshot spine and left-join strict option features. The existing strict forecast validator remains authoritative for rows containing option evidence, while a composing live validator separately validates truly optionless ML rows. Workflow safety uses separate `can_score` and `can_refresh` signals.

**Tech Stack:** Python 3.11, DuckDB SQL, pandas, LightGBM-compatible inference, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-independent-iv-and-ml-coverage-design.md`

## Global Constraints

- Do not weaken `v_eligible_straddles`, `v_straddle_features`, quote-quality thresholds, or reconciliation coverage.
- Do not backfill `em_straddle_pct` or other strict option fields from IV-only evidence.
- Keep the public display-method enum backward compatible; IV-only estimates remain `options_indicative` with `selected_options_details.estimator = "atm_iv"`.
- Keep exact trained-horizon model matching. Never apply a model to a different lead-time feature vector.
- Any row carrying option evidence must still pass the existing strict forecast validator.
- Non-options critical reconciliation failures remain fatal.
- Point-in-time cutoff and earnings-session expiry rules remain unchanged.

---

### Task 1: Independent Contract-Level IV Display Fallback

**Files:** `tools/frontend_data/display_forecast.py`, `tools/frontend_data/display_payloads.py`, `tools/tests/test_independent_iv_fallback.py`

- [x] Add RED tests for mismatched-strike call/put IV, single-sided clean IV, and invalid-IV historical fallback.
- [x] Confirm RED in GitHub CI before production changes.
- [x] Add `_select_indicative_iv()` using raw contract IV, event-spanning expiries, existing display spread/ATM limits, independent call/put selection, and `IV × sqrt(DTE/365)`.
- [x] Insert IV-only selection after relaxed same-strike straddle selection and before historical fallbacks.
- [x] Preserve pair failure provenance and keep public method `options_indicative`.
- [x] Allow display validation for indicative `no_same_strike_pair` provenance.

### Task 2: Remove Strict-Straddle Row Dependency from Live ML Scoring

**Files:** `scripts/daily_score.py`, `apps/ml/tests/test_daily_score_option_independence.py`

- [x] Add a RED SQL-contract test proving current scoring uses strict options as the row spine.
- [x] Confirm RED in GitHub CI.
- [x] Add `snapshot_spine` from upcoming events × valid pre-event OHLCV dates.
- [x] LEFT JOIN `v_straddle_features` by symbol/snapshot/event-spanning expiry.
- [x] Keep option-derived model features nullable instead of filtering rows away.
- [x] Prefer realized-vol/snapshot close for spot before ATM-strike proxy.
- [x] Keep exact trained-horizon filtering unchanged.

### Task 3: Validate ML-Only Forecast Rows Without Weakening Strict Option Validation

**Files:** `apps/ml/ml/live_forecast_validation.py`, `apps/ml/tests/test_pipeline_validation_optionless_ml.py`, `scripts/validate_ml_pipeline.py`, `scripts/provenance_model_rollback.py`

- [x] Add a RED regression showing the existing live gate rejects an otherwise valid optionless ML row.
- [x] Confirm RED in GitHub CI: 144 existing backend/ML tests passed and the new optionless test failed on the strict-options assumptions.
- [x] Add a composing `validate_live_forecast_artifact()`.
- [x] Route every row carrying any strict option evidence through the unchanged `validate_forecast_artifact()`.
- [x] Validate truly optionless ML rows for model bundle/horizon, feature-vector schema, finite point/quantile outputs, quantile monotonicity, absolute-move identity, point-in-time dates, and freshness.
- [x] Treat partially populated option rows as strict rows so they fail closed rather than becoming ML-only.
- [x] Use the live validator in nightly validation and provenance rollback.

### Task 4: Continue Refresh Safely on a Verified Published Options Fallback

**Files:** `scripts/options_snapshot_resilience.py`, `.github/workflows/data-refresh.yml`, `scripts/tests/test_options_fallback_refresh.py`, `scripts/tests/test_calendar_options_hold_contract.py`

- [x] Keep `can_score` strict: true only when the newest options candidate is decision-safe.
- [x] Add `can_refresh`: true for an accepted candidate or a locally restored published fallback.
- [x] Preserve fallback source date and `state = "fallback"`; never relabel it accepted.
- [x] Emit `strict_options_candidate_accepted` and `refresh_scoring_allowed` in status metadata.
- [x] Restore/rebuild/reconcile/verify fallback before downstream scoring.
- [x] Gate ML scoring, forecast validation, R2 forecast publication, Neon import, and frontend generation on `can_refresh`.
- [x] Keep unrelated critical failures fatal.

### Task 5: Preserve ML Availability Diagnostics

**Files:** `tools/frontend_data/display_forecast.py`, `tools/frontend_data/display_payloads.py`, `tools/tests/test_ml_status_propagation.py`

- [x] Add RED tests for explicit `unavailable_inputs`, `unavailable_model`, and `unavailable_event` statuses.
- [x] Normalize missing-ML status conservatively and default to `unavailable_inputs`.
- [x] Pass through upstream `ml_status` from display payloads when available.
- [x] Do not infer or substitute untrained horizons in the display resolver.

### Task 6: Full Verification and Pull Request

**PR:** `#141 Fix options/ML forecast coverage before historical fallback`

- [ ] Run/observe the full GitHub CI matrix on the final branch head.
- [ ] Run/observe security checks on the final branch head.
- [ ] Inspect any failing job logs and fix only verified regressions.
- [ ] Review the final PR diff for these invariants:

```text
strict option views/thresholds unchanged
em_straddle_pct not backfilled from IV-only data
IV-only display fallback precedes historical fallback
ML rows can exist with null option features
rows with any strict option evidence still use the old strict validator
exact model-horizon matching unchanged
can_score remains strict while can_refresh permits verified fallback refresh
fallback state retains its actual older options source date
unrelated critical reconciliation failures remain fatal
```

- [ ] Update the PR body with root cause, implementation, RED→GREEN evidence, and any remaining operational caveats.
- [ ] Mark the PR ready only after required CI is green.
