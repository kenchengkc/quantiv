# Extended-Hours Quotes and Options Data Migration Plan

**Date:** May 17, 2026  
**Status:** Recommended implementation plan  
**Scope:** Targeted extended-hours stock quotes, broader options coverage, and a safe path from DoltHub toward a paid options provider.

---

## Executive Decision

Do these as two separate projects.

1. **Extended-hours stock quotes:** implement now, using Alpaca as a targeted quote source for today's earnings reporters only.
2. **Options data migration:** do not hard-replace DoltHub with Massive.com Starter immediately. Add Massive as a provider overlay, validate the exact fields and history available under the paid credentials, then promote it only after overlap testing.

Do **not** move the options system to "API calls only." That would push the expensive work into Vercel and user requests. Keep the current analytical spine:

```text
provider APIs / flat files
  -> normalized Parquet in R2
  -> DuckDB views
  -> ML scoring + frontend JSON
  -> Vercel serves compact artifacts and small quote caches
```

The core correction to the earlier plan is this:

- Massive's option-chain REST snapshot is useful for current/future coverage and includes IV/Greeks/OI.
- Massive flat files are bulk historical trades, quotes, and aggregates. They are not a direct historical replacement for DoltHub's `bid`, `ask`, `vol`, `delta`, `gamma`, `theta`, `vega`, `rho` table unless we build an adapter and possibly compute IV/Greeks ourselves.

---

## Current System Baseline

### Stock quote path

Current production shape:

```text
Cloudflare Worker cron
  -> Vercel /api/cron/refresh-prices
  -> Finnhub /quote
  -> Upstash Redis quote:SYMBOL
  -> /api/stocks/batch-price
  -> frontend pages
```

Current constraints:

- The Vercel route already writes `quote:SYMBOL` cache keys.
- `batch-price` mostly reads Redis/memory and warms stale symbols during the quote-refresh window.
- The current quote window is regular-session focused plus a post-close buffer.
- The user need is specifically BMO and AMC earnings movement outside regular hours.

### Options data path

Current production shape:

```text
GitHub Actions nightly
  -> pull R2
  -> sync DoltHub options / earnings / OHLCV / volhist
  -> write Parquet
  -> rebuild DuckDB views
  -> score upcoming earnings
  -> build apps/frontend/public JSON
  -> commit JSON
  -> Vercel deploy
```

Important local contract:

`scripts/setup_duckdb_from_parquet.py` expects option rows with:

```text
date
act_symbol
expiration
strike
call_put        # currently "Call" / "Put"
bid
ask
vol             # implied volatility
delta
gamma
theta
vega
rho
```

The ML feature builder relies on these fields for ATM detection, straddle mid, IV-based expected move, event-vol decomposition, and IV/RV features. Any provider migration must preserve or explicitly replace that canonical contract.

---

## Provider Facts and Design Implications

### Alpaca

Useful facts from current Alpaca docs:

- Alpaca's stock latest quotes endpoint is `GET https://data.alpaca.markets/v2/stocks/quotes/latest` and accepts comma-separated `symbols` plus `feed` values such as `iex`, `sip`, and `delayed_sip`.
- Alpaca's multi-symbol stock snapshots endpoint is `GET https://data.alpaca.markets/v2/stocks/snapshots` and returns latest trade, latest quote, minute bar, daily bar, and previous daily bar per symbol.
- Alpaca Basic is the default free market-data plan for trading accounts. It uses IEX for real-time U.S. equities, with higher limits available on paid plans.
- Alpaca's trading support page describes full extended-hours trading as 4:00 AM-9:30 AM ET and 4:00 PM-8:00 PM ET.
- IEX's own system hours are narrower: 8:00 AM-5:00 PM ET, with pre-market 8:00-9:30 AM and post-market 4:00-5:00 PM.

Design implication:

- Alpaca is a good free candidate for targeted extended-hours stock quotes.
- Treat IEX as a "good enough quote signal," not a consolidated SIP truth source.
- With the free Basic tier, only schedule Alpaca stock refreshes inside IEX system hours: 8:00-9:24 AM ET for BMO reporters and 4:00-5:00 PM ET for AMC reporters. If we later pay for SIP, widen the windows toward 4:00 AM-8:00 PM ET.
- Before rollout, run a credentialed probe during those IEX windows for active earnings reporters. If IEX has stale or sparse quotes for the names that matter, fall back to `delayed_sip`, Marketdata.app, or another quote source for extended hours.

### Massive.com

Useful facts from current Massive docs:

- The option-chain snapshot endpoint is `GET /v3/snapshot/options/{underlyingAsset}`.
- It returns a paginated chain snapshot with contract details, latest quote, latest trade when plan-entitled, open interest, implied volatility, and optional Greeks.
- The endpoint default limit is small and maxes at 250 results per page, so full chains require pagination.
- Flat Files are delivered through an S3-compatible interface.
- Massive Flat Files are organized by asset class and data type. Options flat-file datasets include OPRA trades, quotes, minute aggregates, and day aggregates.
- Massive states flat files are generally available around 11:00 AM ET the following day.
- Flat-file plan access varies by dataset. Some official flat-file pages show specific options datasets not included in lower plans. Verify with the actual Starter credentials before assuming all options flat files are available.

Design implication:

- Use Massive REST snapshots first for current and forward-looking coverage.
- Do not assume Massive Starter flat files can replace historical DoltHub option rows with IV/Greeks.
- If historical IV/Greeks are required, either compute them from quotes/underlying prices or use a provider that explicitly sells historical chain snapshots with IV/Greeks.

### Marketdata.app as fallback

Useful facts from Marketdata.app docs:

- Option chain and quote endpoints expose bid/ask, mid, last, open interest, volume, IV, and Greeks.
- Historical chain requests are supported, but credit usage scales by option symbols returned.
- Docs warn that bulk/full-chain use can consume credits quickly.

Design implication:

- Marketdata.app is a better fallback if the requirement is historical IV/Greeks without building our own calculations.
- It is less attractive as the broad daily bulk source because credit use needs careful filtering.

### Vercel and Cloudflare

Useful facts from current docs:

- Vercel Hobby Cron Jobs are limited to once per day and imprecise scheduling, so the external Cloudflare Worker trigger remains the right design for minute-level refreshes.
- Vercel Functions can run longer with Fluid Compute, but usage still counts as Function invocations, Active CPU, and memory duration.
- Cloudflare Cron weekday numbers use `1 = Sunday`, unlike many cron systems. Use named weekdays like `MON-FRI` and `TUE-SAT`.
- Cloudflare Workers Free includes 100,000 requests/day and 5 Cron Triggers per account.

Design implication:

- Keep Cloudflare as the scheduler and Vercel as the authenticated quote-refresh target.
- Do not put provider secrets in Cloudflare unless the Worker is directly calling the provider. For this plan, Alpaca and Massive secrets should live in Vercel/GitHub Actions, not Cloudflare.
- Avoid long sleep/pacing loops where possible. Extended-hours Alpaca pulls should be batched and finish quickly.

---

## Phase 0 - Provider and Data Validation

Do this before writing production migration code.

### Alpaca validation script

Create a temporary local script or notebook that:

1. Reads `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY`.
2. Calls `/v2/stocks/snapshots?symbols=AAPL,NVDA,TSLA&feed=iex`.
3. Calls `/v2/stocks/quotes/latest?symbols=AAPL,NVDA,TSLA&feed=iex`.
4. Runs during:
   - 8:00-9:24 AM ET
   - 4:00-5:00 PM ET
   - regular session
5. Records:
   - HTTP status
   - latest trade timestamp
   - latest quote timestamp
   - bid/ask midpoint
   - latest trade price
   - previous daily close

Pass criteria:

- At least one of latest trade or quote midpoint updates during the extended-hours windows for active, liquid symbols.
- Timestamps are recent enough to avoid showing stale prices as live prices.
- `prevDailyBar.c` is usable as previous regular close, or we can fall back to the cached previous close from the last regular refresh.

Fail criteria:

- IEX does not update materially during the required windows.
- Timestamps are stale for common liquid reporters.
- Previous close is missing often enough to break percent-change display.

### Massive validation script

Create `scripts/probe_massive_capabilities.py` before creating a full sync script.

It should:

1. Read `POLYGON_API_KEY` first; accept `MASSIVE_API_KEY` only as a local compatibility alias.
2. Fetch option-chain snapshot for `AAPL`, `NVDA`, and one smaller earnings symbol.
3. Paginate until complete for one symbol.
4. Record fields present for each contract:
   - underlying
   - option contract identifier
   - expiration
   - strike
   - contract type
   - bid
   - ask
   - last quote timestamp
   - implied volatility
   - delta/gamma/theta/vega
   - open interest
   - underlying price
5. Verify which Flat File datasets are available with the actual credentials.
6. Save a small raw sample under `data/ref/provider_samples/massive/YYYY-MM-DD/`.

Pass criteria:

- REST option-chain snapshots provide enough data to build the current canonical schema for current-day snapshots.
- Pagination works for large names.
- Field names and nesting are stable enough to map cleanly.

Fail criteria:

- Starter lacks critical fields, or fields are too frequently missing near ATM contracts.
- Flat files are not available for the required options dataset.
- Historical endpoint access does not cover the requested lookback.

---

## Phase 1 - Extended-Hours Stock Quotes

### Product behavior

Use targeted session refreshes:

| Session | ET window | Universe | Provider | User outcome |
|---|---:|---|---|---|
| Premarket | 8:00-9:24 AM | today's BMO earnings reporters | Alpaca Basic IEX | see live premarket reaction before open when IEX has prints |
| Regular | 9:30 AM-4:00 PM | existing full priority universe | Finnhub or existing path | unchanged regular quote experience |
| After-hours | 4:00-5:00 PM | today's AMC earnings reporters | Alpaca Basic IEX | see live first-hour post-report reaction when IEX has prints |

Recommendation: start after-hours at **4:00 PM ET**, not 4:45 PM. The first 15-45 minutes are where much of the earnings move happens. If the existing full-universe post-close settle is still needed, run it as a lower-priority close-settle job, not instead of the AMC reporter refresh.

### Implementation shape

#### 1. Time helpers

Update `apps/frontend/lib/marketHours.ts` to expose:

```ts
export type RefreshSession = 'premarket' | 'regular' | 'afterhours';

export function etParts(now?: Date): {
  weekday: string;
  isoDate: string;
  minutes: number;
};

export function currentRefreshSession(now?: Date): RefreshSession | null;

export function isPremarketWindowET(now?: Date): boolean;
export function isAfterhoursWindowET(now?: Date): boolean;
```

Important details:

- Compute `todayIso` in ET, not from server local time or UTC.
- Keep holiday/weekend filtering centralized.
- Decide whether half-days matter. For now, full-day handling is acceptable because earnings-report extended windows are still usually relevant.

#### 2. Worker trigger

Update `workers/refresh-prices/src/index.ts` to:

- Mirror only the small ET window classifier.
- Use `event.scheduledTime` where possible for deterministic scheduled execution.
- Return early outside target windows.
- Append `?window=premarket`, `?window=regular`, or `?window=afterhours`.
- Do not include Alpaca secrets.

Use named weekdays in `wrangler.toml`:

```toml
[triggers]
crons = [
  "* 12-22 * * MON-FRI"
]
```

This is a UTC superset of:

- 8:00 AM-5:00 PM ET during EDT
- 8:00 AM-5:00 PM ET during EST

The Worker ET check must still drop non-window minutes, holidays, and weekends.

#### 3. Alpaca client

Create `apps/frontend/lib/alpaca.ts`.

Use snapshots as the primary endpoint:

```text
GET https://data.alpaca.markets/v2/stocks/snapshots?symbols=AAPL,NVDA&feed=iex
```

For each symbol:

- Prefer `latestTrade.p` if timestamp is recent.
- Else use `(latestQuote.bp + latestQuote.ap) / 2` if both sides are valid and recent.
- Use `prevDailyBar.c` for previous close when available.
- Fall back to existing cached `previousClose` if needed.
- Refuse to write a "live" quote if the timestamp is stale beyond a session threshold, for example 10 minutes during active extended-hours refresh.

Do not use a fictional `filter=ext` parameter unless the credentialed docs prove it exists for the chosen endpoint.

#### 4. Vercel cron route

Update `apps/frontend/app/api/cron/refresh-prices/route.ts`.

Behavior:

- `window=regular`: existing Finnhub path, ideally without changing semantics.
- `window=premarket`: read current ET date, load this week's events, filter `earnings_date === todayIso` and timing BMO.
- `window=afterhours`: same, but timing AMC.
- `force=1`: allow manual testing with an explicit `window`.

Reporter filtering:

- The weekly file is named by Monday, not by date. Compute the Monday for the current ET date.
- Read `apps/frontend/public/weeks/<monday>.json`.
- Deduplicate symbols.
- Normalize timing values: `bmo`, `before_market_open`, `before_open`, `amc`, `after_market_close`, `after_close`.

Cache write:

Keep backward compatibility with current Redis shape, but add metadata:

```ts
type CachedQuote = {
  at: number;
  tick: Tick;
  source?: 'finnhub' | 'alpaca_iex';
  session?: 'premarket' | 'regular' | 'afterhours';
};
```

Older readers can ignore `source` and `session`.

#### 5. Batch price response

Update `apps/frontend/app/api/stocks/batch-price/route.ts` so cached Alpaca records are not mislabeled as Finnhub.

Recommended response extension:

```ts
{
  source: 'finnhub' | 'alpaca_iex' | 'mixed' | 'unavailable',
  session: 'premarket' | 'regular' | 'afterhours' | 'closed',
  marketOpen: boolean,
  quoteRefreshActive: boolean,
  data: [
    {
      symbol: string,
      price: number | null,
      previousClose: number | null,
      change: number | null,
      changePct: number | null,
      source?: 'finnhub' | 'alpaca_iex',
      session?: 'premarket' | 'regular' | 'afterhours'
    }
  ]
}
```

Frontend pages can keep working if they ignore the new fields, but this makes debugging and labels accurate.

#### 6. Polling behavior

For symbol pages and grids:

- During regular hours: keep current polling behavior.
- During premarket: fast-poll only BMO reporters for today.
- During after-hours: fast-poll only AMC reporters for today.
- Outside those targeted sessions: slow-poll or serve stale cache.

Do not fast-poll every ticker during extended hours.

### Phase 1 verification

Local and manual:

1. Unit-test ET date helpers around UTC midnight.
2. Unit-test Cloudflare cron classifier with representative EDT and EST timestamps.
3. Test BMO filter with a fixture week JSON.
4. Test AMC filter with a fixture week JSON.
5. Run `npm run type-check --workspace @quantiv/frontend`.
6. Use `wrangler dev` scheduled-event testing for Worker classification.

Credentialed provider tests:

1. At 5:00 AM ET, hit:
   - `/api/cron/refresh-prices?window=premarket&force=1`
   - `/api/stocks/batch-price?symbols=<today BMO reporters>`
2. At 4:05 PM ET, hit:
   - `/api/cron/refresh-prices?window=afterhours&force=1`
   - `/api/stocks/batch-price?symbols=<today AMC reporters>`
3. Confirm:
   - non-null prices for active names
   - recent timestamps
   - correct `source`
   - correct `session`
   - Redis keys written once per symbol

Production monitoring:

- Log counts only, not every symbol every minute.
- Track `universe`, `fetched`, `failed`, provider status, and session.
- Alert or inspect manually if `failed / universe > 0.5` for liquid reporters.

---

## Phase 2 - Options Coverage and Massive Migration

### Correct target architecture

Do not write Massive output directly over the existing DoltHub path on day one.

Use provider-separated raw data plus canonical normalized output:

```text
data/parquet/options_raw/provider=dolthub/...
data/parquet/options_raw/provider=massive_snapshot/...
data/parquet/options_canonical/provider=dolthub/...
data/parquet/options_canonical/provider=massive/...
data/parquet/options_features/...
```

Then DuckDB can expose stable views:

```sql
v_options_raw
v_options
v_atm_options
v_straddle_features
```

Those views should read from canonical rows, not from provider-specific raw layouts.

### Canonical options schema

Use the current DoltHub-compatible columns as the minimum contract, with additive metadata:

```text
date                  DATE
provider              VARCHAR
provider_snapshot_ts  TIMESTAMP
act_symbol            VARCHAR
option_symbol         VARCHAR
expiration            DATE
strike                DOUBLE
call_put              VARCHAR       # must be "Call" or "Put" for current views
bid                   DOUBLE
ask                   DOUBLE
mid                   DOUBLE
vol                   DOUBLE        # implied volatility
delta                 DOUBLE
gamma                 DOUBLE
theta                 DOUBLE
vega                  DOUBLE
rho                   DOUBLE
open_interest         DOUBLE
volume                DOUBLE
underlying_price      DOUBLE
quote_ts              TIMESTAMP
trade_ts              TIMESTAMP
quality_flags         VARCHAR[]
```

Compatibility rule:

- The current view code can continue to expose the old columns.
- New columns can support better filtering and quality checks.
- `call_put` must remain `Call` / `Put` until `setup_duckdb_from_parquet.py` is updated. Mapping to `C` / `P` would break the current straddle join.

### Massive ingestion phases

#### Phase 2A - Current snapshot overlay

Create `scripts/sync_massive_snapshots.py`.

Purpose:

- Improve current/future coverage for upcoming earnings and missing symbols.
- Avoid full-market bulk ingestion before provider behavior is proven.

Universe:

- Earnings reporters for the next 30-45 calendar days.
- Watchlist symbols.
- Optional high-liquidity ETF/mega-cap list.
- Symbols missing from DoltHub but present in earnings calendar.

Endpoint:

```text
GET /v3/snapshot/options/{underlyingAsset}
```

Request strategy:

- Fetch one underlying at a time.
- Use filters if available:
  - expiration greater than or equal to today
  - expiration less than or equal to earnings date + 60 days
  - optionally strike bands around underlying price after first page reveals underlying price
- Paginate with `next_url`.
- Respect 429 responses and rate-limit headers.
- Save raw JSON samples for debugging.

Normalization:

- Parse contract details into `act_symbol`, `expiration`, `strike`, `call_put`.
- Map latest quote bid/ask to canonical `bid` and `ask`.
- Map `implied_volatility` to `vol`.
- Map `greeks.delta`, `greeks.gamma`, `greeks.theta`, `greeks.vega`.
- Set `rho` null if Massive does not return it.
- Map `open_interest`.
- Preserve source timestamps.
- Add quality flags for missing quote, missing IV, missing Greeks, stale quote, crossed market, zero bid/ask, deep ITM missing Greeks.

Write path:

```text
data/parquet/options_canonical/provider=massive_snapshot/year=YYYY/month=MM/YYYY-MM-DD.parquet
```

Do not overwrite DoltHub parquet files.

#### Phase 2B - DuckDB union view

Update `scripts/setup_duckdb_from_parquet.py` so:

- DoltHub canonical rows remain available.
- Massive canonical rows are unioned in.
- Deduplication prefers Massive for a symbol/date/expiration/strike/call_put when Massive has fresher non-null bid/ask/IV.
- DoltHub remains fallback for dates and symbols Massive does not cover.

Example precedence:

```text
1. massive_snapshot row with bid/ask and IV and quote_ts on target date
2. dolthub row with bid/ask and IV
3. massive row with bid/ask but missing IV, if IV can be computed later
```

#### Phase 2C - Frontend and scoring use expanded coverage

If the DuckDB views stay compatible, these should mostly continue working:

- `scripts/daily_score.py`
- `tools/build_frontend_data.py`
- symbol JSON generation
- screener JSON generation

But still verify:

- New symbols appear.
- Expected move math works.
- Straddle table renders.
- Missing Greeks/IV rows do not erase symbols that DoltHub previously handled.

#### Phase 2D - Historical strategy

Do not treat Massive Starter as a proven historical IV/Greeks replacement until validated.

Choose one of these paths:

**Path 1: forward-collection only**

- Keep DoltHub historical data for model training.
- Append Massive snapshots going forward.
- Use Massive mainly to expand current and future coverage.
- This is the recommended first path.

**Path 2: compute IV/Greeks internally from Massive flat-file quotes**

- Use Massive options quotes/day/minute aggregates if Starter credentials include them.
- Reconstruct EOD or snapshot-like bid/ask per contract.
- Join underlying stock price.
- Compute IV and Greeks with a vetted library/model.
- Store computed values with `provider=massive_computed`.
- This is more engineering work and should be treated as a separate project.

**Path 3: use a historical chain provider**

- Use Marketdata.app or another provider for historical chain/quote endpoints with IV/Greeks.
- Filter aggressively around earnings and ATM strikes to control credits.
- This is the best fallback if historical IV/Greeks are more important than lowest monthly cost.

### What not to do

Do not:

- Delete DoltHub sync immediately.
- Archive active DoltHub parquet before overlap testing.
- Assume Massive flat files contain daily IV/Greeks snapshots.
- Put Massive broad-chain requests behind a public Vercel route.
- Fetch full chains on user page load.
- Store full option chains in Upstash Redis.
- Train the ML model on a mixed provider dataset without provider-quality indicators.

### R2 retention policy

R2 should remain active storage, not archival-only.

Recommended layout:

```text
r2://quantiv-data/parquet/options_canonical/provider=dolthub/...
r2://quantiv-data/parquet/options_canonical/provider=massive_snapshot/...
r2://quantiv-data/parquet/options_raw/provider=massive_snapshot/...
r2://quantiv-data/models/...
r2://quantiv-data/forecasts/...
r2://quantiv-data/archive/dolthub_options_YYYYMMDD/...
```

Archive DoltHub only after:

- 30-60 days of Massive overlap comparison.
- At least one successful weekly retrain with no metric regression.
- A restore procedure has been tested.

### GitHub Actions changes

Do not replace `sync_dolthub.py` in the first Massive PR.

Instead:

1. Add `scripts/probe_massive_capabilities.py`.
2. Add `scripts/sync_massive_snapshots.py`.
3. Add an optional workflow step after DoltHub sync:

```yaml
- name: Sync Massive option snapshots
  if: env.POLYGON_API_KEY != ''
  run: python scripts/sync_massive_snapshots.py --days-ahead 45
```

4. Rebuild DuckDB views.
5. Run freshness and coverage checks.

Only after validation:

- Mark DoltHub options sync as legacy.
- Keep DoltHub earnings/OHLCV/volhist syncs unless replacing them separately.
- Keep R2 push/pull and DuckDB build.

### Massive verification

Coverage:

1. Count distinct option underlyings from DoltHub.
2. Count distinct underlyings from Massive snapshots for next 45 days.
3. Count earnings reporters with usable options features before and after Massive.
4. Produce a daily coverage report:

```text
date
events_total
events_with_dolthub_options
events_with_massive_options
events_with_any_options
newly_covered_by_massive
missing_after_both
```

Quality:

For overlapping symbols/dates:

- Compare ATM strike chosen by each provider.
- Compare ATM call/put bid/ask.
- Compare straddle percent.
- Compare ATM IV.
- Compare event expected move.
- Flag large differences.

ML:

- Do not train on Massive rows until quality checks exist.
- Add `provider` and `quality_flags` as analysis columns, even if not model features.
- Run walk-forward validation before replacing production models.
- Promote only if key metrics are within tolerance and new coverage improves user-visible pages.

Recommended tolerance:

- Feature extraction row count increases or stays stable.
- No major drop in T-1/T-2/T-3 validation coverage.
- MAE/RMSE within 5-10% of baseline for existing names.
- Missing-feature rate does not materially increase.

---

## Proposed File Changes

### Phase 1

| File | Change |
|---|---|
| `apps/frontend/lib/marketHours.ts` | Add ET date/session helpers and explicit premarket/afterhours windows |
| `apps/frontend/lib/alpaca.ts` | New Alpaca snapshot client |
| `apps/frontend/app/api/cron/refresh-prices/route.ts` | Branch by `window`, filter BMO/AMC reporters, write quote metadata |
| `apps/frontend/app/api/stocks/batch-price/route.ts` | Preserve and expose cached quote source/session |
| `workers/refresh-prices/src/index.ts` | Session classifier and `window` query param |
| `workers/refresh-prices/wrangler.toml` | Named-weekday UTC superset cron patterns |

### Phase 2

| File | Change |
|---|---|
| `scripts/probe_massive_capabilities.py` | New provider validation script |
| `scripts/sync_massive_snapshots.py` | New REST snapshot sync for upcoming earnings universe |
| `scripts/setup_duckdb_from_parquet.py` | Add canonical provider union once Massive normalization exists |
| `scripts/check_options_provider_quality.py` | New coverage/quality comparison report |
| `.github/workflows/daily-refresh.yml` | Add optional Massive overlay step, do not remove DoltHub initially |
| `scripts/README.md` | Document provider scripts and migration status |

Files that should not need Phase 2 changes if the canonical views are done correctly:

- `apps/frontend/components/EarningsScreener.tsx`
- `apps/frontend/app/[symbol]/page.tsx`
- `tools/build_frontend_data.py`
- most ML trainer code

---

## Rollout Plan

### Rollout 1 - Extended-hours quotes

1. Build helpers and Alpaca client.
2. Add feature flag:

```text
ENABLE_ALPACA_EXTENDED_QUOTES=1
```

3. Deploy with flag off.
4. Run `force=1` tests with flag on in preview.
5. Enable in production for 1 week.
6. Monitor quote freshness and function usage.

Rollback:

- Disable `ENABLE_ALPACA_EXTENDED_QUOTES`.
- Worker still calls route, but route can skip extended windows.
- Existing regular Finnhub path remains intact.

### Rollout 2 - Massive capability probe

1. Add probe script.
2. Run locally with credentials.
3. Commit sample schema report, not raw paid data.
4. Decide whether Starter is sufficient.

Rollback:

- No production behavior change.

### Rollout 3 - Massive snapshot overlay

1. Add snapshot sync for next 45 days.
2. Store provider-separated parquet.
3. Generate coverage report only.
4. Do not use rows for production scoring yet.

Rollback:

- Remove optional workflow step.
- Delete provider-specific parquet prefix if needed.

### Rollout 4 - Canonical union

1. Add Massive rows into DuckDB canonical view behind an env flag:

```text
ENABLE_MASSIVE_OPTIONS_OVERLAY=1
```

2. Build frontend JSON in preview.
3. Compare symbol JSON and screener JSON.
4. Enable production after coverage and quality pass.

Rollback:

- Disable `ENABLE_MASSIVE_OPTIONS_OVERLAY`.
- DoltHub-only view remains available.

### Rollout 5 - Historical/provider decision

After 30-60 days:

- If Massive snapshot quality is strong, keep collecting forward.
- If historical IV/Greeks are needed, compare:
  - Massive flat-file plus internal IV/Greeks computation
  - Marketdata.app targeted historical chain pulls
  - keeping DoltHub history indefinitely

Only then decide whether to deprecate DoltHub options sync.

---

## Cost and Usage Expectations

### Extended-hours quotes

Expected usage:

- One Cloudflare scheduled event per active minute in a UTC superset.
- One Vercel function invocation per active market minute after Worker filtering.
- One Alpaca batched request per extended-hours minute, only if today's BMO/AMC universe is non-empty.
- Small Upstash writes, one per reporter symbol.

Risk:

- Vercel invocation count increases, but the route should be short and cheap.
- Upstash writes increase on earnings-heavy days.
- Alpaca IEX may be sparse in some extended-hours periods.

Mitigation:

- Do not refresh non-reporters.
- Do not fetch one Alpaca request per symbol if the batch endpoint works.
- Add stale timestamp filtering.
- Add feature flag.

### Options provider

Expected usage:

- GitHub Actions does provider sync, not Vercel.
- R2 remains the storage layer.
- Massive REST calls scale with upcoming earnings universe, not all U.S. optionable symbols.

Risk:

- Full chains for large names require pagination.
- Snapshot fields may be missing for some contracts.
- Historical flat-file availability may not match the marketing summary.
- Historical IV/Greeks may require internal computation or another provider.

Mitigation:

- Probe first.
- Store raw samples.
- Keep DoltHub.
- Add quality reports.
- Do not overwrite canonical data until validated.

---

## Open Questions

1. Does Alpaca Basic IEX provide fresh enough quotes for the exact extended-hours periods needed for BMO/AMC reporters?
2. Under the actual Massive Starter credentials, which options flat-file datasets are available?
3. Does Massive Starter include last quote and latest trade in option-chain snapshots for all needed contracts?
4. How often are Greeks missing near ATM contracts?
5. Is 2 years of Massive history enough for the ML target, or should DoltHub remain the historical backbone?
6. Should current production show extended-hours quote labels in the UI, or is price movement enough for the first release?
7. Do we want to pay for Marketdata.app only for targeted historical chain backfills if Massive history is insufficient?

---

## Final Recommendation

Implement **Phase 1** after the Alpaca probe passes. It is narrowly scoped, preserves the existing cache shape, and directly improves the earnings workflow.

Implement **Phase 2** as a provider overlay, not a replacement. Massive is worth testing because its option-chain snapshot can expand current coverage, but the Starter plan should not be treated as a proven DoltHub replacement until the exact entitled datasets and schema are validated.

The right long-term system is:

```text
DoltHub historical baseline
  + Massive current/future snapshot coverage
  + optional targeted historical provider/computed IV layer
  -> canonical Parquet
  -> DuckDB
  -> ML/features/frontend JSON
```

This keeps cost controlled, improves ticker coverage, and avoids putting model-critical historical data behind live per-user API calls.

---

## Sources

- Alpaca Market Data API overview: <https://docs.alpaca.markets/v1.3/docs/about-market-data-api>
- Alpaca latest stock quotes endpoint: <https://docs.alpaca.markets/v1.3/reference/stocklatestquotes>
- Alpaca stock snapshots endpoint: <https://docs.alpaca.markets/reference/stocksnapshots-1>
- Alpaca real-time stock data docs: <https://docs.alpaca.markets/docs/real-time-stock-pricing-data>
- Alpaca extended-hours trading support page: <https://alpaca.markets/support/extended-hours-trading>
- Massive option-chain snapshot docs: <https://massive.com/docs/rest/options/snapshots/option-chain-snapshot>
- Massive Flat Files quickstart: <https://massive.com/flat-files>
- Massive options flat files overview: <https://massive.com/docs/flat-files/options>
- Massive options trades flat-file docs: <https://massive.com/docs/flat-files/options/trades/2023>
- Massive options day aggregates flat-file docs: <https://massive.com/docs/flat-files/options/day-aggregates/2015/07>
- Marketdata.app options overview: <https://www.marketdata.app/data/options/>
- Marketdata.app option-chain docs: <https://www.marketdata.app/docs/api/options/strikes/>
- Marketdata.app plan limits: <https://www.marketdata.app/docs/account/plan-limits/>
- Cloudflare Cron Triggers docs: <https://developers.cloudflare.com/workers/configuration/cron-triggers/>
- Cloudflare Workers limits: <https://developers.cloudflare.com/workers/platform/limits/>
- Vercel Cron Jobs usage and pricing: <https://vercel.com/docs/cron-jobs/usage-and-pricing>
- Vercel Functions limits: <https://vercel.com/docs/functions/limitations/>
