import { NextRequest, NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Polygon free tier is 5 req/min. With Upstash as a shared cache across all
// Vercel function instances, stale-while-revalidate on top, and per-instance
// request coalescing, we can comfortably serve ~50 concurrent users across
// dozens of symbols inside that budget.
//
// Tiers per symbol:
//   fresh   : < FRESH_TTL_MS old, return as-is
//   stale   : < STALE_TTL_MS old, return immediately + kick off background refresh
//   expired : fetch inline (blocking)
const FRESH_TTL_MS = 60_000;
const STALE_TTL_MS = 5 * 60_000;
const LOCK_TTL_SEC = 10;

type PriceResponse = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  updated: string;
  source: 'polygon' | 'cache' | 'stale' | 'unavailable';
};

type CacheEntry = { at: number; body: PriceResponse };

const memCache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<PriceResponse>>();

function cacheKey(symbol: string) {
  return `price:${symbol}`;
}
function lockKey(symbol: string) {
  return `price:lock:${symbol}`;
}

async function readCache(symbol: string): Promise<CacheEntry | null> {
  const local = memCache.get(symbol);
  if (local && Date.now() - local.at < FRESH_TTL_MS) return local;
  const redis = getRedis();
  if (!redis) return local ?? null;
  try {
    const raw = await redis.get<CacheEntry>(cacheKey(symbol));
    if (raw) {
      memCache.set(symbol, raw);
      return raw;
    }
  } catch {
    // Redis unavailable — fall back to local cache (or null).
  }
  return local ?? null;
}

async function writeCache(symbol: string, body: PriceResponse) {
  const entry: CacheEntry = { at: Date.now(), body };
  memCache.set(symbol, entry);
  const redis = getRedis();
  if (!redis) return;
  try {
    // Keep in Redis for 2× stale so a brief outage doesn't empty cache.
    await redis.set(cacheKey(symbol), entry, { ex: Math.floor((STALE_TTL_MS * 2) / 1000) });
  } catch {
    // Ignore — local cache already updated.
  }
}

// Cross-instance lock: the first caller to SET NX wins the upstream call.
// Losers return false and rely on the cache that the winner will write.
async function acquireLock(symbol: string): Promise<boolean> {
  const redis = getRedis();
  if (!redis) return true; // single-instance dev: let it through
  try {
    const ok = await redis.set(lockKey(symbol), '1', { nx: true, ex: LOCK_TTL_SEC });
    return ok === 'OK';
  } catch {
    return true;
  }
}

async function fetchSnapshot(symbol: string, apiKey: string): Promise<PriceResponse | null> {
  const url = `https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/${encodeURIComponent(symbol)}?apiKey=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error(`polygon snapshot ${res.status}`);
  const json = await res.json();
  const t = json?.ticker;
  const last = t?.lastTrade?.p ?? t?.day?.c ?? null;
  const prev = t?.prevDay?.c ?? null;
  if (last === null && prev === null) return null;
  const change = typeof last === 'number' && typeof prev === 'number' ? last - prev : null;
  const changePct = change !== null && prev ? change / prev : null;
  return {
    symbol,
    price: last,
    previousClose: prev,
    change,
    changePct,
    updated: new Date().toISOString(),
    source: 'polygon',
  };
}

async function fetchPrevClose(symbol: string, apiKey: string): Promise<PriceResponse> {
  const url = `https://api.polygon.io/v2/aggs/ticker/${encodeURIComponent(symbol)}/prev?adjusted=true&apiKey=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`polygon prev ${res.status}`);
  const json = await res.json();
  const bar = json?.results?.[0];
  const close = bar?.c ?? null;
  return {
    symbol,
    price: close,
    previousClose: close,
    change: null,
    changePct: null,
    updated: new Date().toISOString(),
    source: 'polygon',
  };
}

async function fetchFromPolygon(symbol: string, apiKey: string): Promise<PriceResponse> {
  const snap = await fetchSnapshot(symbol, apiKey);
  if (snap) return snap;
  return fetchPrevClose(symbol, apiKey);
}

// Coalesce concurrent calls to the same symbol inside this instance. Combined
// with the Redis NX lock, the whole fleet makes at most one upstream call per
// symbol per lock window.
function refreshPrice(symbol: string, apiKey: string): Promise<PriceResponse> {
  const existing = inflight.get(symbol);
  if (existing) return existing;
  const p = (async () => {
    try {
      const body = await fetchFromPolygon(symbol, apiKey);
      await writeCache(symbol, body);
      return body;
    } finally {
      inflight.delete(symbol);
    }
  })();
  inflight.set(symbol, p);
  return p;
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const raw = url.searchParams.get('symbol');
  if (!raw) {
    return NextResponse.json({ error: 'symbol required' }, { status: 400 });
  }
  const symbol = raw.toUpperCase().replace(/[^A-Z.\-]/g, '').slice(0, 10);

  const cached = await readCache(symbol);
  const age = cached ? Date.now() - cached.at : Infinity;

  if (cached && age < FRESH_TTL_MS) {
    return NextResponse.json({ ...cached.body, source: 'cache' });
  }

  const apiKey = process.env.POLYGON_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      {
        symbol,
        price: cached?.body.price ?? null,
        previousClose: cached?.body.previousClose ?? null,
        change: null,
        changePct: null,
        updated: new Date().toISOString(),
        source: 'unavailable',
      } satisfies PriceResponse,
      { status: 200 },
    );
  }

  // Stale-while-revalidate: serve the stale value now, refresh in background
  // if nobody else holds the lock. Losers of the lock contest just skip the
  // fetch; the winner's writeCache will benefit them on the next request.
  if (cached && age < STALE_TTL_MS) {
    if (await acquireLock(symbol)) {
      refreshPrice(symbol, apiKey).catch(() => {});
    }
    return NextResponse.json({ ...cached.body, source: 'stale' });
  }

  try {
    const body = await refreshPrice(symbol, apiKey);
    return NextResponse.json(body);
  } catch (e) {
    if (cached) {
      return NextResponse.json({ ...cached.body, source: 'stale' });
    }
    return NextResponse.json(
      { error: (e as Error).message, symbol, source: 'unavailable' },
      { status: 502 },
    );
  }
}
