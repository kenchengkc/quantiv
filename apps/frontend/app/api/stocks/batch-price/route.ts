import { NextRequest, NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Finnhub free tier: 60 calls/min, per-symbol /quote endpoint. We cache each
// symbol individually with stale-while-revalidate so a typical dashboard view
// (20–40 symbols on screen) only hits Finnhub during the 60s refresh window,
// then serves from cache until the next expiry. Cold loads are capped by
// CONCURRENCY to avoid tripping the rate limit on first paint.
const FRESH_TTL_MS = 60_000;
const STALE_TTL_MS = 5 * 60_000;
const CONCURRENCY = 10;

type Tick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};
type Cached = { at: number; tick: Tick };

const memCache = new Map<string, Cached>();
const inflight = new Map<string, Promise<Tick | null>>();

const redisKey = (symbol: string) => `quote:${symbol}`;

async function readCache(symbol: string): Promise<Cached | null> {
  const mem = memCache.get(symbol);
  if (mem) return mem;
  const redis = getRedis();
  if (!redis) return null;
  try {
    const raw = await redis.get<Cached>(redisKey(symbol));
    if (raw) {
      memCache.set(symbol, raw);
      return raw;
    }
  } catch {
    /* ignore */
  }
  return null;
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
      if (tick) await writeCache(symbol, { at: Date.now(), tick });
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
  ).slice(0, 200);

  if (symbols.length === 0) {
    return NextResponse.json({ error: 'symbols required' }, { status: 400 });
  }

  const apiKey = process.env.FINNHUB_API_KEY;

  // Classify each symbol as cold (fetch blocking), stale (fetch blocking),
  // near-fresh (background revalidate), or fresh (cache hit).
  const entries = await Promise.all(
    symbols.map(async (s) => ({ s, entry: await readCache(s) })),
  );
  const now = Date.now();
  const blocking: string[] = [];
  const background: string[] = [];
  for (const { s, entry } of entries) {
    if (!entry) {
      blocking.push(s);
      continue;
    }
    const age = now - entry.at;
    if (age >= STALE_TTL_MS) blocking.push(s);
    else if (age >= FRESH_TTL_MS) background.push(s);
  }

  if (apiKey && blocking.length > 0) {
    await mapLimit(blocking, CONCURRENCY, (s) => refreshSymbol(s, apiKey));
  }
  if (apiKey && background.length > 0) {
    // Fire-and-forget; the next request will see fresh data.
    void mapLimit(background, CONCURRENCY, (s) => refreshSymbol(s, apiKey));
  }

  let latestAt = 0;
  const data = symbols.map((s) => {
    const entry = memCache.get(s);
    if (!entry) return nullTick(s);
    if (entry.at > latestAt) latestAt = entry.at;
    return entry.tick;
  });

  return NextResponse.json({
    updated: latestAt ? new Date(latestAt).toISOString() : null,
    source: apiKey ? 'finnhub' : 'unavailable',
    data,
  });
}
