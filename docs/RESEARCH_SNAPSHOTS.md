# Research snapshots

Quantiv research snapshots turn a transient browser research state into a reproducible, content-addressed artifact that can be attached to a notebook, memo, Slack thread, or model-review record.

JSON snapshots use schema `quantiv.research-snapshot.v1` and currently support two kinds:

- `earnings_screener`
- `symbol_research`

## Screener export

The earnings screener exposes JSON and CSV exports through:

```text
GET /api/research/screener-snapshot
```

The endpoint consumes the same URL-state vocabulary as `/screener`:

- `q`
- `sp500=1`
- `minSpot`
- `timing=bmo|amc`
- `ml=1`
- `preset=rich_vol|cheap_vol|big_movers|confident|crowded`
- `sort`
- `dir=asc|desc`

`format=csv` requests CSV; JSON is the default.

The snapshot includes the canonicalized query and the exact ordered filtered rows, so the exported artifact records both *what the researcher asked* and *what the validated release returned*.

## Symbol export

Ticker pages expose the same JSON/CSV/ID controls through:

```text
GET /api/research/symbol-snapshot?symbol=AAPL
```

The symbol snapshot captures the complete static `symbols/<ticker>.json` research payload together with current forecast-evidence and publication-control identifiers. It is appropriate for preserving the end-of-day state used in a memo or notebook.

The CSV representation is a single research row: scalar top-level fields remain ordinary columns while nested structures such as expected move, term rows, earnings history, and provider enrichment are serialized as JSON cells. JSON is the canonical representation for the full symbol state.

Unsafe ticker strings are rejected before any path construction.

## Snapshot identity

The snapshot ID is:

```text
sha256:<64 lowercase hex characters>
```

Shared identity helpers live in `apps/frontend/lib/researchSnapshot.server.ts`. The ID is calculated from canonical JSON containing the research state and its evidence envelope.

For screener snapshots this includes:

- source screener version, `as_of_date`, and generated time;
- current forecast evidence receipt and quality status;
- current publication-control state;
- the canonicalized screener query;
- decision-scope declarations;
- the exact ordered result rows.

For symbol snapshots it includes:

- symbol and symbol-payload `as_of_date`;
- current forecast evidence receipt and quality status;
- current publication-control state;
- decision-scope declarations;
- the complete static symbol research payload.

The `snapshot_id` itself is appended after hashing, so identical evidence + research state produce the same ID. Changed source data, evidence receipt, control state, query, or result produces a different ID.

CSV exports carry the same ID in the `X-Quantiv-Snapshot-Id` response header and in their `snapshot_id` column.

## What is intentionally excluded

Research snapshots do **not** include ephemeral live quote overlays.

Symbol snapshots also exclude the optional spot-updated model overlay. Those are useful browser views, but mixing them into a content-addressed research artifact would imply synchronized point-in-time reproducibility that the current quote/options contract does not provide.

The JSON therefore declares the boundary explicitly:

```json
{
  "decision_scope": "end_of_day_research",
  "live_trading_eligible": false,
  "live_quote_overlay_included": false,
  "spot_updated_prediction_included": false
}
```

The final field is present on symbol snapshots. A future execution-grade snapshot would need synchronized timestamped option quotes, quote depth/liquidity, latency controls, and an explicit point-in-time contract.

## Persistence boundary

The export is **content-addressed but not server-persisted by ID** in this version. The downloaded JSON/CSV is immutable evidence because its contents determine its ID; the product does not yet promise that `/snapshots/<id>` will retrieve old exports after the underlying public release changes.

That distinction is deliberate. Persistent snapshot retrieval should be added only with an explicit immutable storage contract rather than by silently retaining mutable browser state.

## Query semantics

`apps/frontend/lib/screenerResearch.ts` defines the export-side screener query contract and mirrors the current product filter/sort semantics, including preset thresholds and deterministic ticker tie-breaking. Unit tests pin those semantics so an export cannot silently reinterpret a saved URL.

Longer term, the interactive screener and Research Lab should consume this same serialized query object directly so filtering, saved sets, exports, historical cohort execution, Python, and API requests share one implementation.

## Verification

```bash
npm run test --workspace=apps/frontend -- --run
npm run test:e2e --workspace=apps/frontend -- research-snapshot-export.spec.ts
```
