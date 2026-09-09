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
