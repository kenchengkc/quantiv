# Historical ML Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run historical out-of-sample admission and publish only qualified, currently eligible ML forecasts.

**Architecture:** Seal historical test rows before fitting a fresh native candidate. Sign per-head/cohort evaluation evidence, derive a restricted serving bundle without changing tested weights, and admit it through the existing guarded control plane.

**Tech Stack:** Python, pandas, DuckDB, LightGBM, Ed25519, Parquet, GitHub Actions, R2.

**Spec:** `docs/superpowers/specs/2026-10-15-historical-ml-admission.md` implements the design approved in chat.

## Global Constraints

- Use descriptive branch names without the prohibited prefix; omit assistant branding.
- Preserve the primary checkout's existing changes.
- Keep freshness, source integrity, native inference, monitoring, rollback, and no historical-median fallback.
- Minimum 200 distinct held-out events and matched baseline rows per admitted lead/cohort; coverage tolerance 0.08.
- Test exact model artifacts; future refits require their own evidence.
- No backdated prospective ledger rows, synthetic provider receipts or forced promotion.

## Review Focus

- A full-history-fitted candidate must not pass a historical holdout gate.
- Changing a model, input, prediction, split or capability invalidates its evidence.
- A single passing cohort cannot authorize another cohort or undeclared head.
- Stale current inputs must hold activation even when historical metrics pass.
- Legacy rollback bundles must remain readable and retain their original identity.

## Task 1: Sealed historical evaluation

**Files:** new `apps/ml/ml/historical_admission.py`, `scripts/research/historical_model_admission.py`, corresponding ML/script tests.

**Interfaces:** `evaluate_historical_admission(...) -> (receipt_path, signed_receipt)`; `verify_historical_admission_receipt(receipt, *, bundle_manifest=None, bundle_dir=None, public_key=None, evidence_root=None) -> payload`. Receipt contains `bundle_id`, `model_validation_receipt_id`, exact `artifacts`, `reservation_id`, `policy`, `splits`, per-cell `horizons`, `admitted_cohorts`, and `evidence_files`.

- [x] Add failing tests for consumed labels, changed files, wrong identities, missing heads, unsafe timing, mixed passing/failing cohorts and pre-promotion evaluation.
- [x] Copy and seal test/development inputs before the production trainer can read them; use a shared candidate-wide boundary across all requested heads.
- [x] Score exact native point/quantile artifacts using canonical prediction semantics; grade three adequately sampled chronological blocks and clustered uncertainty.
- [x] Sign and verify complete receipts; run focused tests and retain red/green evidence.

## Task 2: Signed serving capabilities

**Files:** `apps/ml/ml/model_bundle.py`, new `serving_capabilities.py`, batch/backend/forecast/publication/legacy serving consumers and focused tests.

**Interfaces:** `allowed_cohorts(manifest, horizon, metadata)`; `create_historically_admitted_bundle(source_bundle_dir, bundle_root, *, admission_receipt, source_revision, private_key=None) -> (path, manifest)`.

- [x] Add failing tests for invalid masks, unauthorized cohorts/heads, changed source artifacts and legacy identity compatibility.
- [x] Include optional `serving_capabilities` and `historical_admission` in signed bundle identity while preserving legacy identity when absent.
- [x] Verify source manifest and historical receipt, derive only qualifying heads without modifying native/metadata bytes.
- [x] Enforce capabilities across every serving and final publication path; run focused tests. Bind API database/cache snapshots to signed current source and exact input identities.

## Task 3: Guarded control and operational workflow

**Files:** `scripts/model_control_plane_impl.py`, model promotion/candidate evidence helpers as needed, a dedicated historical admission workflow/runner, control/ordering tests, coverage documentation.

- [x] Add failing control tests: independently verified historical receipt can qualify a candidate without prospective rows; a missing/invalid receipt cannot; stale inputs and failed drift still hold.
- [x] Register the explicitly evaluated candidate without laundering frozen challenger evidence or overwriting existing archives.
- [x] Verify retained historical evidence before allowing the existing promotion/activation/import path.
- [x] Add a reproducible runner: verified data pull, historical source gate, full feature generation, isolated reservation, fit, model validation/package, evaluation, derived serving bundle, exact forecast scoring, guarded decision, archive, activate, import and rebuild public data.
- [x] Persist signed reservation/candidate claims before dependent operations and archive evidence on quality holds; preserve previous champion and prospective monitoring. Add guarded monitoring refresh to rollback handoffs.

## Task 4: Real evaluation and integration

- [x] Materialize the current verified release into the isolated worktree; stage genuine expanded corporate-action controls and source training data. All 25 source tables and shared reservation preflight passed independent census, source, temporal and cross-head overlap checks.
- [ ] Run the sealed historical fit and backtests, retain reports and inspect each passing/failing lead/cohort.
- [x] Run broad Python, contracts and relevant serving checks; obtain independent engine, runtime and serving reviews. Local final verification: 1,407 Python tests, Ruff, actionlint, public contracts and diff checks passed. Frontend/browser verification remains enforced by PR CI.
- [ ] Commit, push, create/attach PR and merge only after CI passes.
- [ ] Run the reviewed workflow and verify exact-bundle production activation, public forecasts, monitoring and rollback state; report factual holds if fresh inputs or quality evidence do not pass.
