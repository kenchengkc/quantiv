// Polygon / Massive grouped-daily aggregates: the entire US stock market's
// daily OHLC in a SINGLE call (~12k tickers). On the free tier this is
// 15-minute delayed intraday (today's bar so far) and end-of-day once a session
// closes — but it lets us refresh the whole universe's price + previous close
// in one or two requests, instead of Finnhub's 60/min single-symbol cursor that
// takes 30+ min to cover everything.
//
// Free-tier note: the per-ticker realtime snapshot endpoint is NOT entitled
// (402/403); grouped-daily aggregates ARE. Verified against POLYGON_API_KEY.
//
// Docs: https://polygon.io/docs/rest/stocks/aggregates/daily-market-summary

const POLYGON_BASE = 'https://api.polygon.io';

export type GroupedClose = { close: number; volume: number };

/** One grouped-daily call → { TICKER: {close, volume} } for the given ET date.
 *  Returns null on a non-trading day (Polygon reports resultsCount 0) or error,
 *  so callers can walk back to the previous session. */
export async function fetchGroupedDailyCloses(
  dateIso: string,
  apiKey: string,
): Promise<Map<string, GroupedClose> | null> {
  const url =
    `${POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/${dateIso}` +
    `?adjusted=true&apiKey=${encodeURIComponent(apiKey)}`;
  let json: {
    resultsCount?: number;
    results?: Array<{ T?: string; c?: number; v?: number }>;
  };
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
    if (!res.ok) return null;
    // Read the body INSIDE the try: AbortSignal.timeout governs the whole
    // request including streaming the ~12k-row, multi-MB payload. Right after
    // Polygon publishes the day's grouped bar that stream can run long, so a
    // body-read timeout must be caught here too — otherwise the TimeoutError
    // escapes and the caller 500s instead of walking back a session.
    json = (await res.json()) as typeof json;
  } catch {
    return null;
  }
  if (!json.results || json.results.length === 0) return null;
  const out = new Map<string, GroupedClose>();
  for (const r of json.results) {
    const sym = (r.T ?? '').toUpperCase();
    if (sym && typeof r.c === 'number' && r.c > 0) {
      out.set(sym, { close: r.c, volume: typeof r.v === 'number' ? r.v : 0 });
    }
  }
  return out.size > 0 ? out : null;
}

/** Walk back from `fromIso` (inclusive) up to `maxBack` calendar days to find
 *  the two most recent sessions with data: the latest (for current/last price)
 *  and the prior (for previous close). */
export async function fetchLatestTwoSessions(
  apiKey: string,
  fromIso: string,
  maxBack = 7,
): Promise<{
  latest: { date: string; closes: Map<string, GroupedClose> };
  prior: { date: string; closes: Map<string, GroupedClose> } | null;
} | null> {
  const sessions: Array<{ date: string; closes: Map<string, GroupedClose> }> = [];
  const start = new Date(`${fromIso}T00:00:00Z`);
  for (let i = 0; i <= maxBack && sessions.length < 2; i++) {
    const d = new Date(start);
    d.setUTCDate(d.getUTCDate() - i);
    const iso = d.toISOString().slice(0, 10);
    const day = d.getUTCDay();
    if (day === 0 || day === 6) continue; // skip weekends without a request
    const closes = await fetchGroupedDailyCloses(iso, apiKey);
    if (closes) sessions.push({ date: iso, closes });
  }
  if (sessions.length === 0) return null;
  return { latest: sessions[0], prior: sessions[1] ?? null };
}
