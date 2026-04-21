import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// In-memory cache (per Vercel function instance). Polygon free tier caps at
// 5 req/min — caching heavily is essential even though this is "live".
const CACHE_TTL_MS = 30_000;
const cache = new Map<string, { at: number; body: PriceResponse }>();

type PriceResponse = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  updated: string;
  source: 'polygon' | 'cache' | 'unavailable';
};

async function fetchSnapshot(symbol: string, apiKey: string): Promise<PriceResponse | null> {
  // Paid-tier endpoint: gives real-time last trade + prev close in one call.
  const url = `https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/${encodeURIComponent(symbol)}?apiKey=${apiKey}`;
  const res = await fetch(url, { next: { revalidate: 0 } });
  if (res.status === 401 || res.status === 403) return null; // signal "not allowed on this plan"
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
  // Free-tier fallback. "price" here is previous close since free plans don't
  // get real-time trades; the UI labels the freshness accordingly.
  const url = `https://api.polygon.io/v2/aggs/ticker/${encodeURIComponent(symbol)}/prev?adjusted=true&apiKey=${apiKey}`;
  const res = await fetch(url, { next: { revalidate: 0 } });
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

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const raw = url.searchParams.get('symbol');
  if (!raw) {
    return NextResponse.json({ error: 'symbol required' }, { status: 400 });
  }
  const symbol = raw.toUpperCase().replace(/[^A-Z.\-]/g, '').slice(0, 10);

  const cached = cache.get(symbol);
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
    return NextResponse.json({ ...cached.body, source: 'cache' });
  }

  const apiKey = process.env.POLYGON_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      {
        symbol,
        price: null,
        previousClose: null,
        change: null,
        changePct: null,
        updated: new Date().toISOString(),
        source: 'unavailable',
      } satisfies PriceResponse,
      { status: 200 },
    );
  }

  try {
    const body = await fetchFromPolygon(symbol, apiKey);
    cache.set(symbol, { at: Date.now(), body });
    return NextResponse.json(body);
  } catch (e) {
    return NextResponse.json(
      { error: (e as Error).message, symbol, source: 'unavailable' },
      { status: 502 },
    );
  }
}
