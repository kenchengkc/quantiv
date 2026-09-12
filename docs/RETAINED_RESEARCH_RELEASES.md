# Retained Research Lab releases

Quantiv's source-level Research Lab must be attributable to immutable bytes that can be recovered after the current public tree changes. The frontend publication system already retains every generated browser-safe file in a content-addressed release, so historical research reuses that control instead of introducing a second archive system.

## Trust chain

A production deployment starts from the source-controlled `apps/frontend/frontend-release.json` control object. It pins an exact frontend release ID plus the byte length and SHA-256 of the immutable release manifest stored in R2. `scripts/materialize_frontend_release.sh` retrieves that exact manifest/archive pair, verifies both against the Git pointer, verifies every archive member, materializes the public tree, and writes the verified manifest bytes last as `public/frontend-release-manifest.json`.

Fallback builds do not receive that attestation. When R2 materialization is skipped, the script deliberately deletes `public/frontend-release-manifest.json` before using the source-controlled compatibility corpus.

For a source-level Research Lab response, `/api/research/cohort` now verifies the chain again at the application boundary:

```text
Git deployment pointer
        │
        ├── authorized release_id
        ├── manifest byte count
        ├── manifest SHA-256
        └── deployment source_revision
        │
        ▼
public/frontend-release-manifest.json
        │
        ├── canonical release_id recomputation
        ├── inventory count/byte reconciliation
        └── exact research-history.json inventory member
        │
        ▼
public/research-history.json
        ├── exact byte count
        └── exact SHA-256
```

If any source-level link is missing or inconsistent, the cohort endpoint fails closed with HTTP 503 rather than silently serving an unretained source universe.

## Cohort provenance

`quantiv.historical-cohort.v1` exposes the verified binding under `source.retained_release`:

- `status = verified` for a source-level universe that matches the authorized retained release;
- `release_id` for the immutable frontend release;
- `manifest_sha256` for the exact retained manifest bytes authorized by Git;
- `source_revision` from the deployment pointer when present;
- `source_artifact.path`, `bytes`, and `sha256` for `research-history.json`.

These fields are included before the cohort `snapshot_id` is computed. A cohort ID therefore binds not only the logical `universe_id`, query, summary, chart sample, and returned rows, but also the exact retained frontend release and exact serialized historical-universe bytes from which the cohort was read.

CSV responses carry the same cohort snapshot ID and expose `X-Quantiv-Retained-Release` so a downloaded table can be tied back to the same retained release.

## Preview behavior

The display-derived migration preview is intentionally different. It reports:

```json
{
  "status": "preview_unverified",
  "release_id": null,
  "manifest_sha256": null,
  "source_revision": null,
  "source_artifact": null
}
```

Preview mode remains useful for clean checkouts and compatibility builds, but it is not promoted to source-level retained evidence.

## Replay boundary

The release ID addresses an immutable manifest/archive pair already retained in R2, and the existing frontend release tooling can verify and materialize that release. This establishes reproducible source bytes for a cohort.

The public Research Lab API still serves the release materialized for the current deployment. It does **not** yet accept an arbitrary historical `release_id` and rematerialize/query that old release on demand. A future replay endpoint can build on this binding without changing cohort identity semantics.
