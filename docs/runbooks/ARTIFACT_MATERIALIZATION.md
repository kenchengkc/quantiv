# Artifact materialization

Production builds must materialize mutable data and model artifacts from their canonical release store instead of assuming generated files are present in the Git checkout.

## Required properties

1. Resolve an immutable release identity before download.
2. Download into a temporary staging directory.
3. Verify expected files, sizes, and cryptographic digests when provided by the release manifest.
4. Atomically promote the staged release into the path consumed by the build or service.
5. Never advance a mutable pointer before immutable members are durable.
6. Fail closed when a required production artifact cannot be verified.

## Local development

Local workflows may use small committed fixtures or explicitly downloaded snapshots. Local convenience must not make production depend on mutable generated state committed to Git.
