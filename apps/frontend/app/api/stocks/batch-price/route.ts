import { NextRequest, NextResponse } from 'next/server';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { getRedis } from '@/lib/redis';
import {
  isNyseRegularSessionET,
  isQuoteRefreshWindowET,
  currentRefreshWindow,
  etDateIso,
} from '@/lib/marketHours';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Finnhub free tier: 60 calls/min, per-symbol /quote endpoint. We cache each
// symbol individually with stale-while-revalidate so a typical dashboard view
// (20–40 symbols on screen) only hits Finnhub during the 60s refresh window,
// then serves from cache until the next expiry. Cold loads are capped by
// CONCURRENCY to avoid tripping the rate limit on first paint.
const FRESH_TTL_MS = 60_000;
// 7 days — bridges Fri close → Mon open weekends and 3-day holidays. Cron
// rewrites every ~30 min during market hours so active entries are always
// far below this limit; the TTL only matters as a safety net when no
// fresh data is being written (weekends, cron downtime, dropped tickers).
const STALE_TTL_MS = 7 * 24 * 60 * 60_000;
const CONCURRENCY = 10;
const BATCH_PRICE_HEADERS = {
  // Shared quote data is already normalized through Upstash; this tiny edge
  // window only absorbs bursts without letting live prices sit stale for a
  // full minute like the former blanket /api rule did.
  'Cache-Control': 'public, max-age=0, s-maxage=5, stale-while-revalidate=10',
};

type Tick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};

/** Cache envelope — must stay in sync with the writer at
 *  apps/frontend/app/api/cron/refresh-prices/route.ts. The `source` and
 *  `session` fields are additive: pre-extended-hours cache entries
 *  written by older deploys have neither, and we treat those as
 *  `finnhub` / `regular` for response labeling. */
type Source = 'finnhub' | 'alpaca_iex';
type Session = 'premarket' | 'regular' | 'afterhours';
type Cached = {
  at: number;
  tick: Tick;
  source?: Source;
  session?: Session;
};

const memCache = new Map<string, Cached>();
const inflight = new Map<string, Promise<Tick | null>>();

const redisKey = (symbol: string) => `quote:${symbol}`;

function publicDir(): string {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public'),
    join(process.cwd(), 'public'),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return candidates[0];
}

function mondayIsoForEtDate(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  const day = d.getUTCDay();
  const delta = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

function loadTodaysReporterSet(
  todayIso: string,
  session: 'bmo' | 'amc',
): Set<string> {
  const candidates = [
    join(publicDir(), 'weeks', `${mondayIsoForEtDate(todayIso)}.json`),
    join(publicDir(), 'weeks', `${todayIso}.json`),
  ];

  for (const path of candidates) {
    if (!existsSync(path)) continue;
    try {
      const payload = JSON.parse(readFileSync(path, 'utf8'));
      const events = (payload.events as {
        ticker?: string;
        earnings_date?: string;
        timing?: string;
      }[] | undefined) ?? [];
      return new Set(
        events
          .filter((e) => {
            if (!e.ticker) return false;
            if ((e.earnings_date ?? '').slice(0, 10) !== todayIso) return false;
            const t = (e.timing ?? '').toLowerCase();
            return session === 'bmo'
              ? t === 'bmo' || t.includes('before')
              : t === 'amc' || t.includes('after');
          })
          .map((e) => e.ticker!.toUpperCase()),
      );
    } catch {
      /* try next candidate */
    }
  }
  return new Set();
}

// Batched cache read — one Upstash MGET round-trip for the whole symbol list
// instead of N individual GETs. For 200 symbols this drops latency from
// ~6 s to ~80 ms whenever Redis is warm. Falls back to memCache for any
// symbols already pulled by a prior request on this instance.
async function readCacheBatch(symbols: string[]): Promise<Map<string, Cached | null>> {
  const result = new Map<string, Cached | null>();
  const need: string[] = [];
  for (const s of symbols) {
    const mem = memCache.get(s);
    if (mem) result.set(s, mem);
    else need.push(s);
  }
  if (need.length === 0) return result;
  const redis = getRedis();
  if (!redis) {
    for (const s of need) result.set(s, null);
    return result;
  }
  try {
    const keys = need.map(redisKey);
    const raws = (await redis.mget<Cached[]>(...keys)) ?? [];
    for (let i = 0; i < need.length; i++) {
      const entry = raws[i] ?? null;
      if (entry) memCache.set(need[i], entry);
      result.set(need[i], entry);
    }
  } catch {
    for (const s of need) result.set(s, null);
  }
  return result;
}

async function writeCache(symbol: string, entry: Cached) {
  memCache.set(symbol, entry);
  const redis = getRedis();
  if (!redis) return;
  try {
    await redis.set(redisKey(symbol), entry, {
      ex: Math.floor((STALE_TTL_MS * 2) / 1000),
    });
  } catch {
    /* ignore */
  }
}

async function fetchQuote(symbol: string, apiKey: string): Promise<Tick | null> {
  const url = `https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(symbol)}&token=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) return null;
  const json = (await res.json()) as {
    c?: number;
    pc?: number;
    d?: number | null;
    dp?: number | null;
  };
  const price = typeof json.c === 'number' && json.c > 0 ? json.c : null;
  if (price === null) return null;
  const previousClose =
    typeof json.pc === 'number' && json.pc > 0 ? json.pc : null;
  const change = typeof json.d === 'number' ? json.d : null;
  // Finnhub returns dp as percent (e.g. 1.23 for +1.23%). We store the
  // decimal form (0.0123) so clients can do `* 100` consistently.
  const changePct = typeof json.dp === 'number' ? json.dp / 100 : null;
  return { symbol, price, previousClose, change, changePct };
}

function refreshSymbol(symbol: string, apiKey: string): Promise<Tick | null> {
  const existing = inflight.get(symbol);
  if (existing) return existing;
  const p = (async () => {
    try {
      const tick = await fetchQuote(symbol, apiKey);
      if (tick) {
        await writeCache(symbol, {
          at: Date.now(),
          tick,
          source: 'finnhub',
          session: 'regular',
        });
      }
      return tick;
    } finally {
      inflight.delete(symbol);
    }
  })();
  inflight.set(symbol, p);
  return p;
}

async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  const worker = async () => {
    while (cursor < items.length) {
      const i = cursor++;
      results[i] = await fn(items[i]);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker),
  );
  return results;
}

const nullTick = (symbol: string): Tick => ({
  symbol,
  price: null,
  previousClose: null,
  change: null,
  changePct: null,
});

/** Finnhub often omits d/dp while c and pc are present; derive so clients are not stuck without % until the next 30s poll. */
function enrichTick(t: Tick): Tick {
  if (t.price == null || !(t.price > 0)) return t;
  const pc = t.previousClose;
  if (pc == null || !(pc > 0)) return t;
  const change = t.change != null ? t.change : t.price - pc;
  const changePct = t.changePct != null ? t.changePct : change / pc;
  return { ...t, change, changePct, previousClose: pc };
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const symbolsParam = url.searchParams.get('symbols') ?? '';
  const symbols = Array.from(
    new Set(
      symbolsParam
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .filter((s) => /^[A-Z][A-Z.\-]{0,9}$/.test(s)),
    ),
  ).slice(0, 1000);   // generous ceiling — MGET + filter regex are O(n), nothing slow about it

  if (symbols.length === 0) {
    return NextResponse.json(
      { error: 'symbols required' },
      { status: 400, headers: BATCH_PRICE_HEADERS },
    );
  }

  const apiKey = process.env.FINNHUB_API_KEY;

  // Non-blocking strategy: read cache for every symbol, respond immediately
  // with whatever we have. Anything missing or stale triggers a fire-and-
  // forget Finnhub fetch so the next poll (client polls every ~10s) sees
  // fresh data. This keeps p99 response time at cache-read latency (~200ms
  // on Upstash REST) regardless of cold-start load size.
  const cache = await readCacheBatch(symbols);
  const now = Date.now();
  const toRefresh: string[] = [];
  for (const s of symbols) {
    const entry = cache.get(s) ?? null;
    if (!entry || now - entry.at >= FRESH_TTL_MS) toRefresh.push(s);
  }

  // The route's own background-warming is Finnhub-only (regular hours).
  // Extended-hours quotes get into the cache via the cron route writing
  // Alpaca ticks — we just serve them here.
  const marketOpen = isNyseRegularSessionET();
  const session: Session | 'closed' = currentRefreshWindow() ?? 'closed';
  const todayIso = etDateIso();
  const extendedReporterSet =
    session === 'premarket' ? loadTodaysReporterSet(todayIso, 'bmo') :
    session === 'afterhours' ? loadTodaysReporterSet(todayIso, 'amc') :
    new Set<string>();
  const requestedExtendedReporter =
    extendedReporterSet.size > 0 && symbols.some((s) => extendedReporterSet.has(s));
  const regularSettleActive = isQuoteRefreshWindowET();
  const quoteRefreshActive = regularSettleActive || requestedExtendedReporter;
  // Only warm the Finnhub cache during the regular-hours window. During
  // extended windows the cron does the writes via Alpaca; we don't want
  // batch-price calling Finnhub /quote outside RTH (that endpoint
  // doesn't update during extended hours anyway and would just spend
  // calls against the 60/min cap).
  const finnhubRefreshSymbols =
    session === 'afterhours'
      ? toRefresh.filter((s) => !extendedReporterSet.has(s))
      : toRefresh;
  if (apiKey && regularSettleActive && finnhubRefreshSymbols.length > 0) {
    void mapLimit(finnhubRefreshSymbols, CONCURRENCY, (s) => refreshSymbol(s, apiKey));
  }

  let latestAt = 0;
  const seenSources = new Set<Source>();
  // Use the batch read map (not only memCache) so this request sees Redis
  // payloads that were just merged into `cache` for cold keys.
  const data = symbols.map((s) => {
    const entry = cache.get(s) ?? null;
    if (!entry) return enrichTick(nullTick(s));
    if (entry.at > latestAt) latestAt = entry.at;
    const src = entry.source ?? 'finnhub';
    seenSources.add(src);
    return {
      ...enrichTick({ ...entry.tick, symbol: entry.tick.symbol || s }),
      source: src,
      session: entry.session ?? 'regular',
    };
  });

  // Count missing prices plus ticks that still have no day %/change after
  // enrichment (needs another Finnhub refresh). Without the latter, the
  // dashboard exited "fast" polling while only EM columns updated.
  const refreshableSymbols =
    regularSettleActive ? new Set(symbols) : extendedReporterSet;
  const isRefreshable = (symbol: string) => refreshableSymbols.has(symbol);
  const pending =
    data.filter((t) => isRefreshable(t.symbol) && t.price === null).length +
    data.filter((t) => (
      isRefreshable(t.symbol) &&
      t.price !== null &&
      (t.changePct === null || t.change === null)
    )).length;

  // Top-level source: pick the dominant cached source so older clients
  // that read this field get a meaningful label. If the cache mixes
  // Finnhub regular-hours entries with Alpaca extended entries (common
  // around 4 PM ET when AMC reporters update via Alpaca while everyone
  // else is still on Finnhub), call it 'mixed'.
  let topSource: 'finnhub' | 'alpaca_iex' | 'mixed' | 'unavailable';
  if (seenSources.size === 0) {
    topSource = 'unavailable';
  } else if (seenSources.size === 1) {
    topSource = [...seenSources][0];
  } else {
    topSource = 'mixed';
  }

  return NextResponse.json(
    {
      updated: latestAt ? new Date(latestAt).toISOString() : null,
      source: topSource,
      session,
      marketOpen,
      quoteRefreshActive,
      pending,
      data,
    },
    { headers: BATCH_PRICE_HEADERS },
  );
}
