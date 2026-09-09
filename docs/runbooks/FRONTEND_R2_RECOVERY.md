# Frontend Publication and R2 Recovery

Use this runbook when immutable frontend publication fails, a deployment pointer cannot be materialized, R2 readback/digest verification fails, or Vercel is serving a release different from the expected publication identity.

This complements [`ARTIFACT_MATERIALIZATION.md`](ARTIFACT_MATERIALIZATION.md), which documents the underlying release format and normal materialization path.

## Trigger symptoms

- `frontend-publication.yml` fails during build, upload, readback, pinning, or deployment;
- `materialize_frontend_release.sh` rejects the deployment pointer, manifest, archive, digest, size, or member verification;
- R2 `frontend/current.json` disagrees with the source-controlled deployment pointer during the transitional architecture;
- the production app is healthy but its release/control snapshot identifies an older/unexpected frontend publication;
- runtime-state R2 materialization fails at the start of a refresh.

## Establish current state

Record before changing pointers:

- Git revision deployed by Vercel;
- `apps/frontend/frontend-release.json` from that revision, if used by the current architecture;
- R2 `frontend/current.json` identity;
- referenced immutable frontend manifest/archive paths, SHA-256 values, and byte counts;
- last successful frontend-publication workflow artifact;
- current runtime-state/data pointer identities;
- public production control/release identity.

Do not advance a pointer simply because an object has a later timestamp.

## Immutable publication failure

If upload of an immutable archive or manifest fails:

1. leave the current pointer unchanged;
2. determine whether any immutable object was partially/fully uploaded;
3. if the same content-derived key already exists, verify its bytes/digest rather than overwriting it;
4. rebuild from the intended source revision/input corpus and rerun publication;
5. require successful remote readback before pointer promotion.

A same-key/different-byte collision is an integrity incident. Do not use `--ignore-existing` or overwrite the object to continue.

## Pointer promotion/readback failure

If immutable objects verify but pointer promotion or readback fails:

- keep the previously validated pointer as production truth;
- preserve the candidate release ID and immutable objects for retry/investigation;
- retry pointer promotion only after confirming the candidate manifest/archive identities exactly match the locally verified candidate;
- verify the remote pointer after write before triggering/accepting deployment.

## Deployment materialization failure

When Vercel/build materialization rejects an artifact:

1. identify the deployment's expected pointer/release ID;
2. validate the pointer schema/path constraints locally or in CI;
3. fetch the exact immutable manifest/archive named by that pointer;
4. verify size and SHA-256 before extraction;
5. run the existing release verifier/materializer;
6. if the immutable release is corrupt/missing, restore deployment to a previously verified pointer/revision rather than changing expected digests to match remote bytes.

Never convert a digest mismatch into a successful deployment by editing the pointer to the observed digest unless that new artifact has independently passed the full publication process and represents an intentional new release.

## R2 runtime-state/data recovery

For runtime-state/data pointer failure:

- preserve the current pointer object and candidate release objects;
- identify the last known-good manifest/archive from workflow evidence;
- verify that release in isolation before repointing;
- use the owning R2/runtime-state scripts rather than manually copying individual data files into a mixed release;
- rebuild DuckDB/control artifacts after materialization when required by the pipeline;
- rerun freshness/reconciliation before allowing scoring/publication.

Restoring an older validated release is preferable to combining files from multiple releases.

## Frontend rollback

A deliberate frontend rollback should select an exact previously verified deployment/release identity. Verify the immutable archive/manifest first, then deploy/materialize that identity. Do not repoint to an arbitrary historical `current.json` capture without verifying the referenced immutable objects.

After rollback, confirm the public application reports the expected release ID and that production smoke passes.

## Verification

Recovery is complete when:

- expected Git/deployment and frontend release identities agree under the active deployment architecture;
- manifest/archive sizes and SHA-256 values verify;
- release member verification/materialization succeeds;
- runtime-state/data reconciliation passes or is intentionally held/degraded;
- Vercel production serves the expected release;
- production smoke succeeds.

## Evidence to retain

Keep the candidate and previous release IDs, manifests/pointers, digest/readback output, workflow artifacts/logs, deployment revision, and rollback/recovery verification. Any same-key/different-byte event or unexpected pointer mutation should create a security/integrity follow-up.
