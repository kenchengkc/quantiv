# Research snapshots

Quantiv research snapshots turn a transient screener view into a reproducible, content-addressed artifact that can be attached to a notebook, memo, Slack thread, or model-review record.

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

## Snapshot identity

JSON uses schema `quantiv.research-snapshot.v1`.

The snapshot ID is:

```text
sha256:<64 lowercase hex characters>
```

It is calculated from canonical JSON containing:

- source screener version, `as_of_date`, and generated time;
- current forecast evidence receipt and quality status;
- current publication-control state;
- the canonicalized screener query;
- decision-scope declarations;
- the exact ordered result rows.

The `snapshot_id` itself is appended after hashing, so identical source evidence + query + rows produce the same ID. A changed query, refreshed data release, changed forecast receipt, changed control state, or changed result row produces a different ID.

CSV exports carry the same ID in the `X-Quantiv-Snapshot-Id` response header and in the `snapshot_id` column on every row.

## What is intentionally excluded

Research snapshots do **not** include the screener's ephemeral live-quote overlay.

The committed screener bundle is validated end-of-day research state. Mixing a best-effort live tick into a content-addressed research artifact would imply point-in-time reproducibility that the current quote path does not provide. The JSON therefore states:

```json
{
  "decision_scope": "end_of_day_research",
  "live_trading_eligible": false,
  "live_quote_overlay_included": false
}
```

A future execution-grade snapshot would need synchronized timestamped option quotes, quote depth/liquidity, latency controls, and an explicit point-in-time contract.

## Persistence boundary

The export is **content-addressed but not server-persisted by ID** in this version. The downloaded JSON/CSV is immutable evidence because its contents determine its ID; the product does not yet promise that `/snapshots/<id>` will retrieve old exports after the underlying public screener release changes.

That distinction is deliberate. Persistent snapshot retrieval should be added only with an explicit immutable storage contract rather than by silently retaining mutable browser state.

## Query semantics

`apps/frontend/lib/screenerResearch.ts` defines the export-side query contract and mirrors the current screener filter/sort semantics, including preset thresholds and deterministic ticker tie-breaking. Unit tests pin those semantics so an export cannot silently reinterpret a saved URL.

Longer term, the interactive screener and Research Lab should consume this same serialized query object directly so filtering, saved sets, exports, historical cohort execution, Python, and API requests share one implementation.

## Verification

```bash
npm run test --workspace=apps/frontend -- --run
npm run test:e2e --workspace=apps/frontend -- research-snapshot-export.spec.ts
```
