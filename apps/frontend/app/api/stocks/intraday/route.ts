import { NextRequest, NextResponse } from 'next/server';
import { fetchIntradayBars, type IntradayPayload } from '@/lib/alpaca';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Intraday IEX bars for the ticker hero sparkline. We cache aggressively
// because each Alpaca call is ~80 bars of JSON and the bars only update
// every 5 min anyway. Two-level cache:
//   1. In-memory per serverless instance (free, sub-ms)
//   2. Upstash Redis shared across instances (fast, 60s TTL)
// Both expire at FRESH_TTL_MS so the chart never lags more than ~1 min
// behind the latest IEX print during a live session.
const FRESH_TTL_MS = 60_000;

type CachedPayload = {
  at: number;
  payload: IntradayPayload;
};

const memCache = new Map<string, CachedPayload>();
const inflight = new Map<string, Promise<IntradayPayload>>();

const redisKey = (symbol: string, tf: string) => `intraday:${symbol}:${tf}`;

async function readCached(
  cacheKey: string,
  redisK: string,
): Promise<IntradayPayload | null> {
  // 1) per-instance memory
  const mem = memCache.get(cacheKey);
  if (mem && Date.now() - mem.at < FRESH_TTL_MS) {
    return mem.payload;
  }
  // 2) Upstash
  const redis = getRedis();
  if (!redis) return null;
  try {
    const cached = (await redis.get(redisK)) as CachedPayload | null;
    if (cached && Date.now() - cached.at < FRESH_TTL_MS) {
      memCache.set(cacheKey, cached);
      return cached.payload;
    }
  } catch {
    // Redis miss / outage: fall through to live fetch.
  }
  return null;
}

async function writeCached(
  cacheKey: string,
  redisK: string,
  payload: IntradayPayload,
): Promise<void> {
  const envelope: CachedPayload = { at: Date.now(), payload };
  memCache.set(cacheKey, envelope);
  const redis = getRedis();
  if (!redis) return;
  try {
    // EX in seconds. Buffer slightly past FRESH_TTL_MS so a memory-hit
    // request between the in-memory expiry and the Redis expiry still
    // sees the entry.
    await redis.set(redisK, envelope, { ex: 120 });
  } catch {
    // Best-effort cache write; ignore Redis errors.
  }
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const symbol = (url.searchParams.get('symbol') ?? '').toUpperCase().trim();
  const tfRaw = url.searchParams.get('timeframe') ?? '5Min';
  // Only accept timeframes the Alpaca helper supports — anything else
  // falls back to 5Min so we don't blow up on a typo.
  const timeframe: '1Min' | '5Min' | '15Min' =
    tfRaw === '1Min' || tfRaw === '15Min' ? tfRaw : '5Min';

  if (!symbol || !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(symbol)) {
    return NextResponse.json({ error: 'invalid symbol' }, { status: 400 });
  }

  const cacheKey = `${symbol}|${timeframe}`;
  const redisK = redisKey(symbol, timeframe);

  // Cache hit?
  const cached = await readCached(cacheKey, redisK);
  if (cached) {
    return NextResponse.json(cached, {
      headers: {
        // CDN edge: serve for 30s, allow stale-while-revalidate for 5
        // min on top of that. Means a popular ticker page hammered by
        // visitors only ever pays Alpaca once a minute.
        'Cache-Control': 's-maxage=30, stale-while-revalidate=300',
      },
    });
  }

  // De-dupe concurrent fetches for the same symbol.
  let pending = inflight.get(cacheKey);
  if (!pending) {
    pending = (async () => {
      try {
        const payload = await fetchIntradayBars(symbol, timeframe);
        await writeCached(cacheKey, redisK, payload);
        return payload;
      } finally {
        inflight.delete(cacheKey);
      }
    })();
    inflight.set(cacheKey, pending);
  }

  try {
    const payload = await pending;
    return NextResponse.json(payload, {
      headers: { 'Cache-Control': 's-maxage=30, stale-while-revalidate=300' },
    });
  } catch {
    // Soft failure: hand the client an empty payload so the hero
    // gracefully falls back to its seeded sparkline instead of
    // surfacing a 500. We don't log the full error to the response
    // body because Alpaca error text occasionally includes secrets.
    return NextResponse.json(
      { bars: [], previousClose: null, asOf: null, error: 'upstream' },
      { status: 200 },
    );
  }
}
