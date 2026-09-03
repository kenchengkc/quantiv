import { NextRequest, NextResponse } from 'next/server';
import { fetchIntradayBars, type IntradayPayload } from '@/lib/alpaca';
import { getRedis } from '@/lib/redis';
import { enforceRateLimit, PUBLIC_RATE_LIMITS } from '@/lib/rateLimit';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Intraday IEX bars for the ticker hero sparkline. Healthy data stays on the
// existing ~60s freshness path, while a separate last-good copy survives
// transient Alpaca/IEX gaps so an overnight provider hiccup cannot blank every
// ticker hero at once.
const FRESH_TTL_MS = 60_000;
const FRESH_REDIS_TTL_SECONDS = 120;
const LAST_GOOD_TTL_SECONDS = 14 * 24 * 60 * 60;

type CachedPayload = {
  at: number;
  payload: IntradayPayload;
};

const memCache = new Map<string, CachedPayload>();
const lastGoodMemCache = new Map<string, IntradayPayload>();
const inflight = new Map<string, Promise<IntradayPayload>>();

const redisKey = (symbol: string, tf: string) => `intraday:${symbol}:${tf}`;
const lastGoodRedisKey = (symbol: string, tf: string) =>
  `intraday:last-good:${symbol}:${tf}`;

function isUsablePayload(payload: IntradayPayload | null | undefined): boolean {
  return Array.isArray(payload?.bars) && payload.bars.length >= 2;
}

async function readCached(
  cacheKey: string,
  redisK: string,
): Promise<IntradayPayload | null> {
  // 1) per-instance memory
  const mem = memCache.get(cacheKey);
  if (
    mem &&
    Date.now() - mem.at < FRESH_TTL_MS &&
    isUsablePayload(mem.payload)
  ) {
    return mem.payload;
  }

  // 2) Upstash. Ignore unusable payloads left by an older deployment so an
  // empty response cannot remain the authoritative fresh cache entry.
  const redis = getRedis();
  if (!redis) return null;
  try {
    const cached = (await redis.get(redisK)) as CachedPayload | null;
    if (
      cached &&
      Date.now() - cached.at < FRESH_TTL_MS &&
      isUsablePayload(cached.payload)
    ) {
      memCache.set(cacheKey, cached);
      return cached.payload;
    }
  } catch {
    // Redis miss / outage: fall through to live fetch.
  }
  return null;
}

async function readLastGood(
  cacheKey: string,
  redisK: string,
): Promise<IntradayPayload | null> {
  const mem = lastGoodMemCache.get(cacheKey);
  if (isUsablePayload(mem)) return mem ?? null;

  const redis = getRedis();
  if (!redis) return null;
  try {
    const payload = (await redis.get(redisK)) as IntradayPayload | null;
    if (isUsablePayload(payload)) {
      lastGoodMemCache.set(cacheKey, payload as IntradayPayload);
      return payload;
    }
  } catch {
    // Best-effort fallback only.
  }
  return null;
}

async function writeGoodPayload(
  cacheKey: string,
  freshRedisK: string,
  lastGoodRedisK: string,
  payload: IntradayPayload,
): Promise<void> {
  if (!isUsablePayload(payload)) return;

  const envelope: CachedPayload = { at: Date.now(), payload };
  memCache.set(cacheKey, envelope);
  lastGoodMemCache.set(cacheKey, payload);

  const redis = getRedis();
  if (!redis) return;
  try {
    await Promise.all([
      redis.set(freshRedisK, envelope, { ex: FRESH_REDIS_TTL_SECONDS }),
      redis.set(lastGoodRedisK, payload, { ex: LAST_GOOD_TTL_SECONDS }),
    ]);
  } catch {
    // Best-effort cache writes; live response is still usable.
  }
}

function intradayResponse(payload: IntradayPayload, fallback = false) {
  return NextResponse.json(payload, {
    headers: {
      'Cache-Control': fallback
        ? 's-maxage=15, stale-while-revalidate=300'
        : 's-maxage=30, stale-while-revalidate=300',
      'X-Quantiv-Intraday': fallback ? 'last-good' : 'fresh',
    },
  });
}

export async function GET(req: NextRequest) {
  const rateLimited = await enforceRateLimit(req, PUBLIC_RATE_LIMITS.stocksIntraday);
  if (rateLimited) return rateLimited;

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
  const freshRedisK = redisKey(symbol, timeframe);
  const lastGoodRedisK = lastGoodRedisKey(symbol, timeframe);

  const cached = await readCached(cacheKey, freshRedisK);
  if (cached) return intradayResponse(cached);

  // De-dupe concurrent live fetches for the same symbol. Empty payloads are
  // deliberately not cached as fresh or last-good data.
  let pending = inflight.get(cacheKey);
  if (!pending) {
    pending = (async () => {
      try {
        const payload = await fetchIntradayBars(symbol, timeframe);
        await writeGoodPayload(cacheKey, freshRedisK, lastGoodRedisK, payload);
        return payload;
      } finally {
        inflight.delete(cacheKey);
      }
    })();
    inflight.set(cacheKey, pending);
  }

  try {
    const payload = await pending;
    if (isUsablePayload(payload)) return intradayResponse(payload);

    // Alpaca can occasionally return an empty bars array without throwing.
    // Prefer the most recently confirmed session to a provider-wide blank UI.
    const lastGood = await readLastGood(cacheKey, lastGoodRedisK);
    if (lastGood) return intradayResponse(lastGood, true);

    return intradayResponse(payload);
  } catch (error) {
    const lastGood = await readLastGood(cacheKey, lastGoodRedisK);
    if (lastGood) {
      console.warn('[intraday] Alpaca unavailable; serving last-good bars', {
        symbol,
        timeframe,
      });
      return intradayResponse(lastGood, true);
    }

    // Soft failure only when there is no previously confirmed session to use.
    // Do not include upstream error text because providers may echo sensitive
    // request details.
    console.warn('[intraday] Alpaca unavailable and no last-good bars exist', {
      symbol,
      timeframe,
      errorName: error instanceof Error ? error.name : 'unknown',
    });
    return NextResponse.json(
      {
        bars: [],
        previousClose: null,
        asOf: null,
        sessionDate: null,
        isCurrentSession: false,
        error: 'upstream',
      },
      {
        status: 200,
        headers: {
          'Cache-Control': 'no-store',
          'X-Quantiv-Intraday': 'unavailable',
        },
      },
    );
  }
}
