# Reproducible research manifests

Quantiv research should be reproducible from the exact bytes that produced a result, not from a filename such as `latest.parquet`.

`scripts/research/research_manifest.py` builds a small content-addressed receipt that pins each declared input by repository-relative path, byte length, SHA-256 digest, Git commit, and research `as_of` time. The manifest itself is also content-addressed with a canonical JSON hash.

```bash
python scripts/research/research_manifest.py build \
  data/processed/events.parquet \
  data/processed/options.parquet \
  --as-of 2026-09-02T20:00:00Z \
  --out artifacts/research/2026-09-02/manifest.json

python scripts/research/research_manifest.py verify \
  artifacts/research/2026-09-02/manifest.json
```

The verifier fails if the manifest body changes, an input disappears, a path escapes the repository root, the file size changes, or the SHA-256 digest no longer matches. This makes research exports and experiment reports independently auditable without introducing a database or experiment-tracking service.

## Intended use

Attach the manifest ID to event-study reports, model diagnostics, exported research bundles, and ad-hoc experiments. A reviewer should be able to answer two questions immediately: **which code revision ran?** and **which exact input bytes did it use?**
