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

// Read-only quote endpoint. The Cloudflare-Worker-driven cron at
// /api/cron/refresh-prices is the ONLY writer of `quote:{SYM}` keys — it
// rotates through the priority symbol list (watchlist + reporters + S&P
// 500) at 55/min within Finnhub's 60/min cap. This route serves whatever
// is in Upstash. Symbols that aren't in the cron's universe will return
// a null tick; the search bar's primary use case (earnings reporters in
// the ±2-week window) is already covered by the cron's tier list.
const FRESH_TTL_MS = 60_000;
const MAX_SYMBOLS = 400;
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

// Reporter set per (date, session) — read at most once per ET day per
// serverless instance. The underlying weeks/*.json regenerates nightly
// during the daily-refresh GitHub Action; a stale memo only affects the
// tail end of an instance's life and is bounded by Vercel's recycle.
const reporterSetCache = new Map<string, Set<string>>();

function loadTodaysReporterSet(
  todayIso: string,
  session: 'bmo' | 'amc',
): Set<string> {
  const key = `${todayIso}|${session}`;
  const cached = reporterSetCache.get(key);
  if (cached) return cached;

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
      const set = new Set(
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
      reporterSetCache.set(key, set);
      return set;
    } catch {
      /* try next candidate */
    }
  }
  const empty = new Set<string>();
  reporterSetCache.set(key, empty);
  return empty;
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
  ).slice(0, MAX_SYMBOLS);

  if (symbols.length === 0) {
    return NextResponse.json(
      { error: 'symbols required' },
      { status: 400, headers: BATCH_PRICE_HEADERS },
    );
  }

  const cache = await readCacheBatch(symbols);

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

  let latestAt = 0;
  const seenSources = new Set<Source>();
  const now = Date.now();
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

  // `pending` is the count of symbols whose Upstash entry is missing OR
  // staler than FRESH_TTL_MS during an active refresh window. Clients use
  // this to decide whether to poll faster — they'll see the count drop as
  // the cron's next rotation lands fresh ticks.
  const refreshableSymbols =
    regularSettleActive ? new Set(symbols) : extendedReporterSet;
  const isRefreshable = (symbol: string) => refreshableSymbols.has(symbol);
  const pending = symbols.reduce((acc, sym) => {
    if (!isRefreshable(sym)) return acc;
    const entry = cache.get(sym) ?? null;
    if (!entry) return acc + 1;
    if (now - entry.at >= FRESH_TTL_MS) return acc + 1;
    return acc;
  }, 0);

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
