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

import { etDateIso } from './marketHours';

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

/** Compact intraday bar shape consumed by the hero sparkline. We only
 *  ship close (`c`) + timestamp (`t`); open/high/low aren't needed to
 *  draw a single-line spark, and dropping them halves the JSON size
 *  shipped to the browser. */
export type IntradayBar = { t: string; c: number };

export type IntradayPayload = {
  bars: IntradayBar[];
  /** Prior session's daily close — used to compute the session % move
   *  if the first regular-hours bar isn't yet present (pre-market). */
  previousClose: number | null;
  /** UTC ISO of the most recent bar; lets the client decide whether the
   *  cached payload is "live enough" to skip a refetch. */
  asOf: string | null;
  /** ET date represented by `bars`. On weekends/holidays this is the
   *  latest session with IEX bars, not today's closed-market date. */
  sessionDate: string | null;
  /** True when `bars` belong to today's ET date. False means the chart is
   *  intentionally showing the latest prior session. */
  isCurrentSession: boolean;
};

/** IEX system hours are 08:00–17:00 ET. We display one ET session's worth of
 *  bars from the 08:00 pre-market start:
 *    • Live session (today, before the IEX day ends): 08:00 → latest print,
 *      so the chart grows through the day instead of showing a stale full day.
 *    • Finished session (after 17:00, or a weekend/holiday): the full
 *      08:00–17:00 day of the most recent session with bars. */
const IEX_SESSION_OPEN_MIN = 8 * 60; // 08:00 ET
const REGULAR_CLOSE_MIN = 16 * 60;   // 16:00 ET — regular-session close

/** Minutes since ET midnight for an ISO timestamp (handles EST/EDT). */
function etMinutesOf(iso: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(iso));
  const m: Record<string, string> = {};
  for (const p of parts) m[p.type] = p.value;
  return ((parseInt(m.hour ?? '0') % 24) * 60) + parseInt(m.minute ?? '0');
}

/** Fetch IEX bars covering the most recent session from its 08:00 ET open.
 *
 *  Time range: now − 6 calendar days → now. The wide lookback guarantees we
 *  reach the last trading session even across a 3-day weekend plus a Monday
 *  holiday (the old 48h window returned zero bars there, which surfaced as
 *  "IEX bars unavailable"). We then keep only the latest session's bars.
 *
 *  Docs: https://docs.alpaca.markets/reference/stockbars-1 */
export async function fetchIntradayBars(
  symbol: string,
  timeframe: '1Min' | '5Min' | '15Min' = '5Min',
): Promise<IntradayPayload> {
  // Alpaca rejects start dates in the future, so we anchor on `now` and walk
  // back far enough to always include the previous session's close plus the
  // most recent session, regardless of intervening weekends/holidays.
  const end = new Date();
  const start = new Date(end.getTime() - 6 * 24 * 60 * 60 * 1000);
  const params = new URLSearchParams({
    timeframe,
    start: start.toISOString(),
    end: end.toISOString(),
    feed: 'iex',
    adjustment: 'raw',
    // Enough for a full 1-min IEX day (08:00–17:00 = 540 bars) across the
    // multi-day window; coarser timeframes need far fewer.
    limit: '1000',
  });
  const data = (await alpacaFetch(
    `/v2/stocks/${encodeURIComponent(symbol)}/bars?${params.toString()}`,
  )) as { bars?: Array<{ t: string; o: number; h: number; l: number; c: number; v: number }> };

  const rawBars = Array.isArray(data?.bars) ? data.bars : [];
  return buildIntradayPayload(rawBars, end);
}

/** Pure transform from raw Alpaca bars to the intraday payload: pick the most
 *  recent session's bars (from its 08:00 ET open) and anchor previousClose to
 *  the prior session's last regular-hours bar. Extracted from fetchIntradayBars
 *  so the session/previousClose logic is unit-testable without an Alpaca
 *  round-trip; `end` defines "today" in ET. */
export function buildIntradayPayload(
  rawBars: Array<{ t: string; c: number }>,
  end: Date,
): IntradayPayload {
  if (rawBars.length === 0) {
    return {
      bars: [],
      previousClose: null,
      asOf: null,
      sessionDate: null,
      isCurrentSession: false,
    };
  }

  const sortedBars = [...rawBars].sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
  const etDate = (iso: string) => etDateIso(new Date(iso));
  const todayKey = etDateIso(end);
  const availableDates = Array.from(new Set(sortedBars.map((b) => etDate(b.t)))).sort();
  // Today if it already has bars (live session), else the latest session that
  // does — the UI labels weekend/holiday sessions explicitly via sessionDate.
  const sessionDate = availableDates.includes(todayKey)
    ? todayKey
    : availableDates[availableDates.length - 1];
  // The full session from the 08:00 ET open. For a live session these bars
  // run up to the latest print (08:00 → now); for a finished session they
  // span the whole 08:00–17:00 IEX day.
  const sessionRawBars = sortedBars.filter(
    (b) => etDate(b.t) === sessionDate && etMinutesOf(b.t) >= IEX_SESSION_OPEN_MIN,
  );
  // Previous close = the prior session's last REGULAR-hours bar (≤16:00 ET).
  // The IEX feed runs to 17:00, so the chronologically last prior-session bar
  // is a post-market print; for an AMC earnings reporter that bar already
  // reflects part of the after-hours move, which would understate the next
  // session's reaction. Excluding ≥16:00 bars anchors to the regular close.
  const priorBars = sortedBars.filter(
    (b) => etDate(b.t) < sessionDate && etMinutesOf(b.t) < REGULAR_CLOSE_MIN,
  );
  const previousClose =
    priorBars.length > 0 ? priorBars[priorBars.length - 1].c : null;

  const bars = sessionRawBars.map((b) => ({ t: b.t, c: b.c }));

  return {
    bars,
    previousClose,
    asOf: bars.length > 0 ? bars[bars.length - 1].t : null,
    sessionDate,
    isCurrentSession: sessionDate === todayKey,
  };
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
