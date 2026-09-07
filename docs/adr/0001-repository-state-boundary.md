# ADR 0001: Separate source control from mutable production state

## Status

Accepted.

## Context

Quantiv has historically committed some generated market-data, model, and frontend publication artifacts so scheduled jobs and deployment builds could consume them directly from the repository. This makes source control carry both implementation and mutable production state, increases repository churn, and weakens the distinction between reproducible source and release artifacts.

Quantiv already has immutable release and signed-pointer patterns in R2 for data/model control-plane state.

## Decision

Git is authoritative for source, schemas, stable configuration, documentation, and small deterministic fixtures. R2 or another explicit release store is authoritative for mutable production datasets, model bundles, generated publication snapshots, receipts, and recovery evidence.

Builds and services must resolve and verify an explicit release before consuming mutable production artifacts. Transitional committed artifacts are allowed only while a consuming path is migrated; each exception should be documented and removed once materialization is available.

## Consequences

- Repository diffs become more code-centric and reviewable.
- Deployments gain an explicit artifact-materialization step.
- Production release identity becomes independent of Git working-tree state.
- Local development needs small fixtures or an explicit snapshot-download command.
