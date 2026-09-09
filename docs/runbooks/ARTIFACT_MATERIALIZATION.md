# Artifact materialization

Production builds must materialize mutable data and model artifacts from their canonical release store instead of assuming generated files are present in the Git checkout.

## Required properties

1. Resolve an immutable release identity before download.
2. Download into a temporary staging directory.
3. Verify expected files, sizes, and cryptographic digests when provided by the release manifest.
4. Atomically promote the staged release into the path consumed by the build or service.
5. Never advance a mutable pointer before immutable members are durable.
6. Fail closed when a required production artifact cannot be verified.

## Cross-run runtime state

Nightly refresh state that is needed across ephemeral runners is owned by the R2 `runtime-state/` namespace rather than Git. `scripts/runtime_state.py` inventories the supported restart/cursor/cache files, builds a content-addressed archive and manifest, verifies every member by size and SHA-256, and materializes the release atomically under `data/`.

Publication is pointer-last: immutable `runtime-state/releases/<release-id>.tar.gz` and `runtime-state/manifests/<release-id>.json` objects are uploaded and read back first; only after verification succeeds may `runtime-state/current.json` advance. The nightly data refresh requires this pointer at startup and publishes the next state release after all state-producing steps complete. Workflows that do not consume this cross-run state may leave the materialization optional.

## Local development

Local workflows may use small committed fixtures or explicitly downloaded snapshots. Local convenience must not make production depend on mutable generated state committed to Git.

## Frontend deployment verification

After full archive verification and materialization, the build publishes the exact
verified manifest at `/frontend-release-manifest.json`. This build-generated file
is excluded from Git and future release inventories (to avoid recursive identities).
A credential-free local fallback removes any previous manifest; it must not claim
to have materialized the pinned release. Production builds need R2 read access.

Production smoke compares the served manifest's byte length and SHA-256 with
`apps/frontend/frontend-release.json` from the triggering checkout. It then checks
the bytes of weekly, screener, control-plane, and model-validation data plus one
week and one symbol against that verified inventory. This is a bounded deployed
sample, not verification of every CDN object or the application code revision.
The build-time verifier checks the full archive. No mutable `latest` pointer is
used as the expected deployment identity.

Missing manifests, mixed-release sample bytes, or an older deployed release fail
the smoke check. Allow up to ten minutes for deployment propagation (40 attempts
at 15-second intervals by default), then inspect the deployment rather than
changing the expected pointer to whatever happens to be live. New main pushes
cancel superseded smoke runs. A deliberate production rollback requires verifying
against its explicitly selected Git-pinned release, not bypassing identity checks.
The smoke workflow also follows successful `Frontend publication release` runs:
their bot commits do not trigger another push workflow. That path checks out main
after producer completion, rather than the producer's pre-publication head SHA.

Run offline verifier regressions with
`node --test scripts/tests/verify_frontend_publication.test.mjs` and materializer
tests with `pytest scripts/tests/test_frontend_release.py scripts/tests/test_frontend_materialization.py`.
