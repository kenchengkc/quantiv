# Frontend performance

Why Quantiv pages can feel slow on first load, and what to fix first. Static
JSON sizes are **not** the main bottleneck — client JS, global shell weight,
and intentional loading gates dominate.

## Quick diagnosis

| Symptom                            | Likely cause                                                                                    |
| ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| Screener / calendar blank for 1–4s | `contentReady` waits for logos + batch quotes + min skeleton delay                              |
| Symbol page slow after navigation  | Chart-heavy client bundle + redundant metadata fetches                                          |
| Every route feels heavy            | Shared shell + React Query + 3 font families                                                    |
| Repeat visit faster                | `next.config.js` CDN cache on `/screener.json`, `/weekly.json` — helps returns, not first paint |

## Static asset sizes (reference)

| File                           | ~Size      | Notes                                                   |
| ------------------------------ | ---------- | ------------------------------------------------------- |
| `public/screener.json`         | 132 KB     | Moderate; parsed on main thread after fetch             |
| `public/weekly.json`           | 44 KB      | Calendar week summary                                   |
| `public/ticker-names.json`     | 276 KB     | Also imported on server **and** fetched again on client |
| `public/ticker-exchanges.json` | 203 KB     | Client fetch on symbol pages                            |
| `public/symbols/*.json`        | ~7 KB each | Per-symbol payload is fine                              |

## Top causes (priority order)

### 1. Screener & calendar loading gates

**Files:** `components/EarningsScreener.tsx`, `components/EarningsGrid.tsx`

The table stays on skeleton until **all** of these are true:

- `loading` finished (client `fetch('/screener.json')`)
- `logosReady` — `preloadTickerLogos` for every event ticker (~100+ Parqet CDN images)
- `quotesReady` — `/api/stocks/batch-price` for up to 400 symbols, or **3.2s** timeout
- `minLoadingDone` — forced **600ms** (screener) / **750ms** (calendar) minimum skeleton

**Quick wins:** Show the table when JSON is loaded; lazy-load logos for visible rows only; fetch quotes in the background without blocking `contentReady`.

### 2. Large client-only bundles

| File                                          |  Lines |
| --------------------------------------------- | -----: |
| `app/(public)/[symbol]/SymbolPageClient.tsx`  | ~1,160 |
| `app/(public)/[symbol]/SymbolPageHeader.tsx`  | ~1,130 |
| `app/(public)/[symbol]/ForecastPanels.tsx`    | ~1,105 |
| `app/(public)/[symbol]/HistoryRiskPanels.tsx` |   ~760 |
| `components/EarningsScreener.tsx`             | ~2,100 |
| `components/EarningsGrid.tsx`                 | ~1,000 |

The symbol page is now separated by visible surface, so header/KPI, forecast,
history/risk, and controller work can change independently. These are still
static client imports, not network-level chunk boundaries; the symbol route
server component validates the ticker and renders the client tree.

**Quick wins:** `dynamic()` for chart / heavy sections; server-pass props for name/exchange to skip client fetches.

### 3. Global layout overhead

**Files:** `app/layout.tsx`, `app/(public)/layout.tsx`, `app/(authenticated)/layout.tsx`

Public routes no longer load `ClerkProvider` or the Clerk browser SDK. Clerk is
scoped to sign-in, sign-up, watchlist, and the protected production-controls
page. The ticker watchlist button resolves authentication through the existing
API only after a click, preserving static ticker delivery.

Every route still loads the client `QueryClientProvider` (`app/providers.tsx`),
`Topbar`, `TickerHoverHost`, Vercel Analytics + Speed Insights, and KaTeX CSS in
`globals.css` (~23 KB on all pages).

React Query is only used for watchlist (`lib/watchlist.ts`); screener/symbol use raw `fetch` + `useState`.

**Quick wins:** KaTeX CSS only on `/about`; trim Google Font weights; scope Query client to routes that need it.

### 4. Duplicate JSON on symbol pages

**Files:** `app/(public)/[symbol]/page.tsx`, `lib/companyNames.ts`, `lib/listingExchanges.ts`

Server imports `ticker-names.json` for metadata; client fetches the same file again plus `ticker-exchanges.json` (~480 KB combined) on mount.

**Quick win:** Pass name/exchange from the server page as props.

### 5. Symbol page quote / intraday polling

**File:** `app/(public)/[symbol]/SymbolPageClient.tsx`

Parallel work on load: symbol JSON, intraday route (`cache: 'no-store'`), batch-price with aggressive polling.

**Quick win:** Defer intraday until hero is visible; use static spot from JSON first.

### 6. Batch-price on critical path

**File:** `app/api/stocks/batch-price/route.ts`

`force-dynamic`, up to 400 symbols per screener request. Called before screener `contentReady`.

**Quick win:** Cap to visible tickers; show stale/static prices first.

## Measurement

```bash
cd apps/frontend
npm run build
npm run test:performance
```

The production-build browser gate takes seven cache-disabled homepage samples
and fails when either lab p90 or the coldest-sample p99 proxy exceeds 1.8s. It
also verifies that the public landing page requests no Clerk resources. This is
a regression guard, not a substitute for field data: Vercel Speed Insights is
the source of truth for real-user p90 and p99 by route and geography.

## What is already optimized

- `next.config.js` sets `Cache-Control` on `/screener.json`, `/weekly.json`, `/weeks/*` for CDN/browser caching on repeat visits.
- Screener uses `react-virtuoso` for row virtualization (DOM stays small once the table is shown).
- Static JSON is prebuilt in CI — no FastAPI round-trip for normal browsing.

## Related

- [README.md](../README.md) — hosting and data pipeline
- [../scripts/README.md](../scripts/README.md) — data/provider pipeline runbook
