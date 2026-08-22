# Evidence receipts

Quantiv publishes one evidence receipt per validated pipeline snapshot. It does
not create lineage records or UI diagrams for every displayed value. A dashboard
can load a single compact receipt and let its metrics refer to shared definition
IDs as the UI audit surface is built out.

## Contract

`quantiv.evidence-receipt.v1` records:

- a deterministic receipt ID derived from the evidence itself;
- SHA-256 fingerprints for the training, model, and forecast artifact bundles
  relevant to the validated stage;
- observation, scoring, and event windows;
- row, symbol, event, and horizon coverage;
- explicit reconciliation counts for duplicate keys, invalid features,
  quantile crossings, IV/straddle handoff errors, and derived-value mismatches;
- the validation result and stable issue codes.

The immutable file contains only the content-addressed receipt. A tiny
`latest_<scope>.json` pointer adds the most recent validation time and immutable
filename, allowing the product to render current trust state with one read.
Absolute runner paths are never published.

## Publication and cost

Forecast receipts live under `data/forecasts/receipts/`; model receipts live
under `data/models/receipts/`. The existing recursive R2 synchronization already
copies both trees. GitHub Actions also retains the full validation reports for
failed runs.

This design adds no Redis keys, Neon rows, service, queue, or per-number request.
The current daily forecast receipt is only a few kilobytes and is generated in
the same fail-closed validation step that already gates publication.

## Safety semantics

1. Validation builds a receipt from the exact artifact bytes and reconciliation
   results.
2. Failed validation returns a nonzero exit code before Neon, R2, or frontend
   publication. Its report remains available as a GitHub Actions artifact.
3. Successful validation writes an immutable content-addressed receipt and
   atomically replaces the small latest pointer.
4. The existing R2 sync publishes the forecast/model and its receipt together.
5. Repeating validation over identical evidence produces the same receipt ID.
   An attempted content collision fails instead of overwriting audit evidence.

The next layer is an exception-first reconciliation manifest for ingestion and
reference-data controls (expected-versus-received counts, missing chains,
corporate-action continuity, mappings, quarantine, and replay status). It can
use the same receipt envelope rather than introducing another persistence path.
