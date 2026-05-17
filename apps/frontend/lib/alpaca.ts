// Minimal Alpaca Market Data REST client. Used by the refresh-prices route
// for pre-market and after-hours quote pulls. The free brokerage tier
// allows 200 calls/min, which is comfortable for the BMO/AMC reporter
// universe (~20-40 tickers per session).
//
// Auth: `ALPACA_KEY_ID` + `ALPACA_SECRET_KEY` env vars. Generated from
// the Alpaca dashboard after signing up for a brokerage account
// (paperwork only — no funding required for data API access).
//
// Endpoint used:
//   GET /v2/stocks/snapshots?symbols=...&feed=iex
//   (latest trade + latest quote + previous daily bar)
//
// The free Basic plan uses the `iex` feed, which carries IEX-venue trades
// during extended hours. Liquid reporters typically print enough on IEX
// during IEX system hours (08:00-17:00 ET) to give a useful live signal —
// but the data is venue-limited, not consolidated SIP. We
// refuse to write a "live" tick whose source timestamp is older than
// MAX_TICK_STALENESS_MS during an active extended-hours window so the
// UI never labels a stale RTH close as a live extended-hours print.
//
// Docs: https://docs.alpaca.markets/reference/stocksnapshots-1

const BASE_URL = 'https://data.alpaca.markets';

/** Refuse to mark a tick "live" if its source timestamp is older than
 *  this during an active extended-hours minute. 10 min covers the
 *  longest typical gap between IEX prints for liquid reporters while
 *  still flagging genuinely stale data as stale (e.g. prior-session
 *  carryover at 08:05 ET, or a sparse-volume name with no recent print). */
const MAX_TICK_STALENESS_MS = 10 * 60 * 1000;

/** Mirrors the Tick shape used by refresh-prices and batch-price routes.
 *  Kept inline here so this file has no cross-route dependency. */
export type Tick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};

interface AlpacaQuote {
  /** Bid price */
  bp: number;
  /** Ask price */
  ap: number;
  /** Quote timestamp (ISO 8601 with nanoseconds) */
  t: string;
}

interface AlpacaTrade {
  /** Trade price */
  p: number;
  /** Trade size */
  s: number;
  /** Trade timestamp (ISO 8601) */
  t: string;
}

interface AlpacaSnapshotResponse {
  [symbol: string]: {
    latestTrade?: AlpacaTrade;
    latestQuote?: AlpacaQuote;
    prevDailyBar?: { c: number };
  };
}

/** Throws on missing credentials or transport / 4xx-5xx errors. The
 *  cron route only calls this when ENABLE_ALPACA_EXTENDED_QUOTES=1, so
 *  missing env vars are a deployment configuration error, not a silent
 *  "no quotes" condition. */
async function alpacaFetch(path: string): Promise<unknown> {
  const keyId = process.env.ALPACA_KEY_ID;
  const secret = process.env.ALPACA_SECRET_KEY;
  if (!keyId || !secret) {
    throw new Error('ALPACA_KEY_ID or ALPACA_SECRET_KEY missing');
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      'APCA-API-KEY-ID': keyId,
      'APCA-API-SECRET-KEY': secret,
      Accept: 'application/json',
    },
    cache: 'no-store',
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Alpaca ${res.status}: ${body.slice(0, 200)}`);
  }
  return res.json();
}

function isFresh(timestamp: string | undefined, nowMs: number): boolean {
  if (!timestamp) return false;
  const t = Date.parse(timestamp);
  if (!Number.isFinite(t)) return false;
  return nowMs - t <= MAX_TICK_STALENESS_MS;
}

/** Fetch the latest extended-hours snapshot for up to ~100 symbols in
 *  one call. Returns a partial map — symbols with no fresh data are
 *  absent. Uses the `iex` feed (free Basic plan).
 *
 *  Selection priority (per symbol):
 *   1. `latestTrade.p` if the trade timestamp is within MAX_TICK_STALENESS_MS
 *   2. midpoint of `latestQuote.bp` and `latestQuote.ap` if the quote
 *      timestamp is within MAX_TICK_STALENESS_MS and both sides are positive
 *   3. otherwise the symbol is omitted (callers can keep the prior cache entry)
 *
 *  `previousClose` is taken from `prevDailyBar.c`; callers should fall
 *  back to their own cached `previousClose` if absent. */
export async function fetchExtendedHoursSnapshot(
  symbols: string[],
): Promise<Map<string, Tick>> {
  const out = new Map<string, Tick>();
  if (symbols.length === 0) return out;

  const symbolsCsv = symbols.map((s) => encodeURIComponent(s)).join(',');
  const data = (await alpacaFetch(
    `/v2/stocks/snapshots?symbols=${symbolsCsv}&feed=iex`,
  )) as AlpacaSnapshotResponse;

  const nowMs = Date.now();
  for (const [rawSymbol, snap] of Object.entries(data)) {
    const symbol = rawSymbol.toUpperCase();
    const trade = snap.latestTrade;
    const quote = snap.latestQuote;

    let price: number | null = null;
    if (trade && isFresh(trade.t, nowMs) && trade.p > 0) {
      price = trade.p;
    } else if (
      quote &&
      isFresh(quote.t, nowMs) &&
      quote.bp > 0 &&
      quote.ap > 0
    ) {
      price = (quote.bp + quote.ap) / 2;
    }

    if (price == null) continue;

    const previousClose = snap.prevDailyBar?.c ?? null;
    const change = previousClose != null ? price - previousClose : null;
    const changePct =
      previousClose != null && previousClose > 0
        ? (price - previousClose) / previousClose
        : null;
    out.set(symbol, { symbol, price, previousClose, change, changePct });
  }
  return out;
}
