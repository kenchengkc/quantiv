# Contributing to Quantiv

Quantiv is a production-oriented quantitative research/data platform. Changes should preserve reproducibility, temporal correctness, artifact provenance, and fail-closed control-plane behavior rather than optimizing only for a green UI or a successful happy-path run.

## Development setup

Use the versions and lockfiles committed to the repository. From the repository root:

```bash
npm ci
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install --no-deps -e apps/ml
```

Copy `.env.example` to the appropriate local configuration file and supply only development credentials. Never commit secrets or production connection strings.

For backend-only work, install the hash-locked backend graph from `apps/backend/requirements.txt` and the local ML package with `--no-deps`.

## Required checks

Run the smallest relevant checks while developing and the full applicable suite before requesting review.

### Python / data pipeline

```bash
ruff check scripts tools
pytest scripts tools -q
```

### Backend / shared ML package

```bash
ruff check apps/backend apps/ml/ml apps/ml/tests
pytest apps/backend/tests apps/ml/tests -q
```

### Frontend

```bash
npm run lint --workspace=apps/frontend
npm run type-check --workspace=apps/frontend
npm run test --workspace=apps/frontend -- --run
npm run build --workspace=apps/frontend
```

Run Playwright/performance tests when changing routing, critical rendering, authentication boundaries, or data presentation.

## Dependency changes

Python production and scheduled-pipeline dependencies are represented by input files and generated hash-locked requirements files. Regenerate locks with the repository's requirements compilation script; do not hand-edit resolved transitive versions or remove hashes to make an install pass.

Node changes must update the appropriate lockfile with the package manifest. Avoid adding libraries for functionality already provided by the standard library or existing dependencies.

A dependency change should be reviewable as a dependency change: explain why it is needed, what runtime surface it affects, and any supply-chain or image-size implications.

## Data and artifact ownership

Treat generated production data as a release artifact, not ordinary source code.

- R2 is the canonical store for hosted Parquet/runtime/model/frontend publication artifacts where the relevant control-plane path has been migrated.
- Immutable objects must be uploaded before mutable discovery pointers are advanced.
- Digests, sizes, schemas, and release identities must be verified before promotion/materialization.
- Do not introduce an unverified `latest` fallback into production code.
- Source-controlled fixtures must be small, intentional, deterministic, and clearly distinguishable from production state.
- Never overwrite a retained artifact merely to make a newer candidate appear valid.

When changing publication semantics, update the corresponding runbook and tests in the same pull request.

## Quantitative research controls

Research changes must respect information availability at the prediction cutoff.

- Do not use future observations, revised values, or post-event fields without an explicit availability/as-of contract.
- Keep training/evaluation splits temporal when the production problem is temporal.
- Preserve walk-forward/purging/embargo controls where applicable.
- Report model-selection families and multiple-testing correction when statistical significance is used to choose among hypotheses/signals/models.
- Do not tune against a nominal holdout and continue describing it as untouched.
- Persist machine-readable validation/control evidence when a decision affects model promotion.

A better headline metric is not sufficient justification for weakening a leakage, provenance, or validation gate.

## Model promotion and serving

Production model changes must flow through the signed champion/challenger control plane. Do not bypass bundle verification, validation receipts, promotion decisions, serving activation receipts, or exact-bundle forecast checks with ad-hoc file copies.

Rollback behavior is a production feature. Changes to model identity, manifests, signatures, activation, or scoring should include a failure/rollback test.

## Security-sensitive changes

Authentication, admin endpoints, HMAC signing, secret handling, R2 object selection, deployment pointers, SQL/database access, CI permissions, and production workflows are security-sensitive.

Use least-privilege workflow permissions, avoid logging secrets, validate external identifiers before using them as paths/queries, and prefer fail-closed behavior at control boundaries. See `SECURITY.md` for vulnerability reporting.

## Pull requests

Keep PRs cohesive and explain:

- the production/research problem being solved;
- invariants that must remain true;
- tests or receipts proving the change;
- migrations/fallbacks and how they are removed;
- rollback/recovery behavior;
- any operational or security impact.

Do not mix generated production data churn with unrelated source changes. If a migration temporarily requires two storage/control paths, describe the exit condition explicitly.

## Documentation

Update the README, architecture docs, runbooks, and `.env.example` when changing supported setup, production topology, control-plane semantics, environment variables, failure modes, or operator actions.
