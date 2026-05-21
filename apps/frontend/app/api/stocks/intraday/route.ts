import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Source = 'alpaca_iex' | 'finnhub' | 'unavailable';

type IntradayPoint = {
  timestamp: string;
  price: number;
  volume: number | null;
};

const SYMBOL_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;
const REGULAR_OPEN_MIN = 9 * 60 + 30;
const REGULAR_CLOSE_MIN = 16 * 60;
const LOOKBACK_DAYS = 7;

function providerSymbol(symbol: string): string {
  return symbol.replace('-', '.');
}

function etParts(timestamp: string): { date: string; minutes: number } | null {
  const d = new Date(timestamp);
  if (!Number.isFinite(d.getTime())) return null;

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const m: Record<string, string> = {};
  for (const p of parts) m[p.type] = p.value;
  const hour = parseInt(m.hour ?? '0', 10) % 24;
  const minute = parseInt(m.minute ?? '0', 10);
  return {
    date: `${m.year ?? '0000'}-${m.month ?? '01'}-${m.day ?? '01'}`,
    minutes: hour * 60 + minute,
  };
}

function normalizePoint(timestamp: string, price: unknown, volume: unknown): IntradayPoint | null {
  const n = typeof price === 'number' ? price : Number(price);
  if (!Number.isFinite(n) || n <= 0) return null;
  return {
    timestamp,
    price: n,
    volume: typeof volume === 'number' && Number.isFinite(volume) ? volume : null,
  };
}

function latestRegularSession(points: IntradayPoint[]): { date: string; data: IntradayPoint[] } | null {
  const byDate = new Map<string, IntradayPoint[]>();
  for (const point of points) {
    const parts = etParts(point.timestamp);
    if (!parts) continue;
    if (parts.minutes < REGULAR_OPEN_MIN || parts.minutes >= REGULAR_CLOSE_MIN) continue;
    const bucket = byDate.get(parts.date) ?? [];
    bucket.push(point);
    byDate.set(parts.date, bucket);
  }

  const dates = [...byDate.keys()].sort().reverse();
  for (const date of dates) {
    const session = (byDate.get(date) ?? [])
      .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
      .filter((point, index, arr) => (
        index === 0 || point.timestamp !== arr[index - 1].timestamp
      ));
    if (session.length >= 2) return { date, data: session };
  }
  return null;
}

async function fetchAlpaca(symbol: string, start: Date, end: Date): Promise<IntradayPoint[] | null> {
  const keyId = process.env.ALPACA_KEY_ID;
  const secret = process.env.ALPACA_SECRET_KEY;
  if (!keyId || !secret) return null;

  const marketSymbol = providerSymbol(symbol);
  const url = new URL('https://data.alpaca.markets/v2/stocks/bars');
  url.searchParams.set('symbols', marketSymbol);
  url.searchParams.set('timeframe', '5Min');
  url.searchParams.set('start', start.toISOString());
  url.searchParams.set('end', end.toISOString());
  url.searchParams.set('feed', 'iex');
  url.searchParams.set('adjustment', 'raw');
  url.searchParams.set('limit', '1000');

  const res = await fetch(url, {
    headers: {
      'APCA-API-KEY-ID': keyId,
      'APCA-API-SECRET-KEY': secret,
      Accept: 'application/json',
    },
    cache: 'no-store',
  });
  if (!res.ok) return null;
  const json = (await res.json()) as {
    bars?: Record<string, Array<{ t?: string; c?: number; v?: number }>>;
  };
  const rows = json.bars?.[marketSymbol] ?? json.bars?.[symbol] ?? [];
  const points = rows
    .map((r) => (r.t ? normalizePoint(new Date(r.t).toISOString(), r.c, r.v) : null))
    .filter((p): p is IntradayPoint => p !== null);
  return points.length ? points : null;
}

async function fetchFinnhub(symbol: string, start: Date, end: Date): Promise<IntradayPoint[] | null> {
  const token = process.env.FINNHUB_API_KEY;
  if (!token) return null;

  const url = new URL('https://finnhub.io/api/v1/stock/candle');
  url.searchParams.set('symbol', symbol);
  url.searchParams.set('resolution', '5');
  url.searchParams.set('from', String(Math.floor(start.getTime() / 1000)));
  url.searchParams.set('to', String(Math.floor(end.getTime() / 1000)));
  url.searchParams.set('token', token);

  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) return null;
  const json = (await res.json()) as {
    s?: string;
    t?: number[];
    c?: number[];
    v?: number[];
  };
  if (json.s !== 'ok' || !json.t || !json.c) return null;
  const points = json.t
    .map((seconds, i) => (
      normalizePoint(
        new Date(seconds * 1000).toISOString(),
        json.c?.[i],
        json.v?.[i],
      )
    ))
    .filter((p): p is IntradayPoint => p !== null);
  return points.length ? points : null;
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const symbol = (url.searchParams.get('symbol') ?? '').trim().toUpperCase();
  if (!SYMBOL_RE.test(symbol)) {
    return NextResponse.json({ error: 'valid symbol required' }, { status: 400 });
  }

  const end = new Date();
  const start = new Date(end.getTime() - LOOKBACK_DAYS * 24 * 60 * 60_000);
  const providers: Array<[Source, () => Promise<IntradayPoint[] | null>]> = [
    ['alpaca_iex', () => fetchAlpaca(symbol, start, end)],
    ['finnhub', () => fetchFinnhub(symbol, start, end)],
  ];

  for (const [source, fetcher] of providers) {
    try {
      const points = await fetcher();
      if (!points) continue;
      const session = latestRegularSession(points);
      if (!session) continue;
      return NextResponse.json(
        {
          symbol,
          source,
          date: session.date,
          updated: new Date().toISOString(),
          data: session.data,
        },
        { headers: { 'Cache-Control': 'no-store' } },
      );
    } catch {
      // Try the next configured provider.
    }
  }

  return NextResponse.json(
    {
      symbol,
      source: 'unavailable',
      date: null,
      updated: new Date().toISOString(),
      data: [],
    },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
