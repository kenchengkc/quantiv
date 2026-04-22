import { NextRequest, NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Polygon's grouped-daily endpoint returns o/h/l/c for every US ticker in one
// call. We cache the whole snapshot keyed by date so the calendar (50+ tickers)
// costs one upstream call instead of fifty.
const FRESH_TTL_MS = 5 * 60_000;
const STALE_TTL_MS = 60 * 60_000;
const LOCK_TTL_SEC = 15;

type GroupedBar = { T: string; o: number; c: number; h: number; l: number; v: number };
type SnapshotEntry = { at: number; date: string; bars: Record<string, GroupedBar> };
type Tick = {
  symbol: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
};

let memSnap: SnapshotEntry | null = null;
let inflight: Promise<SnapshotEntry | null> | null = null;

const SNAP_KEY = 'batch-price:grouped-latest';
const SNAP_LOCK = 'batch-price:lock';

function latestTradingDate(now = new Date()): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  // Roll back over weekends so we never ask Polygon for a non-trading day.
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) {
    d.setUTCDate(d.getUTCDate() - 1);
  }
  // Results for "today" aren't posted until after close, so prefer yesterday.
  d.setUTCDate(d.getUTCDate() - 1);
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) {
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return d.toISOString().slice(0, 10);
}

async function readCache(): Promise<SnapshotEntry | null> {
  if (memSnap && Date.now() - memSnap.at < FRESH_TTL_MS) return memSnap;
  const redis = getRedis();
  if (!redis) return memSnap;
  try {
    const raw = await redis.get<SnapshotEntry>(SNAP_KEY);
    if (raw) {
      memSnap = raw;
      return raw;
    }
  } catch {
    /* ignore */
  }
  return memSnap;
}

async function writeCache(entry: SnapshotEntry) {
  memSnap = entry;
  const redis = getRedis();
  if (!redis) return;
  try {
    await redis.set(SNAP_KEY, entry, { ex: Math.floor((STALE_TTL_MS * 2) / 1000) });
  } catch {
    /* ignore */
  }
}

async function acquireLock(): Promise<boolean> {
  const redis = getRedis();
  if (!redis) return true;
  try {
    const ok = await redis.set(SNAP_LOCK, '1', { nx: true, ex: LOCK_TTL_SEC });
    return ok === 'OK';
  } catch {
    return true;
  }
}

async function fetchGrouped(apiKey: string): Promise<SnapshotEntry | null> {
  const date = latestTradingDate();
  const url = `https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/${date}?adjusted=true&apiKey=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error(`polygon grouped ${res.status}`);
  const json = await res.json();
  const results = (json?.results ?? []) as GroupedBar[];
  const bars: Record<string, GroupedBar> = {};
  for (const b of results) bars[b.T] = b;
  return { at: Date.now(), date, bars };
}

function refreshSnapshot(apiKey: string): Promise<SnapshotEntry | null> {
  if (inflight) return inflight;
  const p = (async () => {
    try {
      const snap = await fetchGrouped(apiKey);
      if (snap) await writeCache(snap);
      return snap;
    } finally {
      inflight = null;
    }
  })();
  inflight = p;
  return p;
}

function tickFrom(snap: SnapshotEntry | null, symbol: string): Tick {
  const bar = snap?.bars[symbol];
  if (!bar || typeof bar.c !== 'number' || typeof bar.o !== 'number') {
    return { symbol, price: null, change: null, changePct: null };
  }
  const change = bar.c - bar.o;
  const changePct = bar.o !== 0 ? change / bar.o : null;
  return { symbol, price: bar.c, change, changePct };
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
  ).slice(0, 200);

  if (symbols.length === 0) {
    return NextResponse.json({ error: 'symbols required' }, { status: 400 });
  }

  let snap = await readCache();
  const age = snap ? Date.now() - snap.at : Infinity;
  const apiKey = process.env.POLYGON_API_KEY;

  if (apiKey && (!snap || age >= FRESH_TTL_MS)) {
    if (!snap || age >= STALE_TTL_MS) {
      try {
        snap = (await refreshSnapshot(apiKey)) ?? snap;
      } catch {
        /* fall through with whatever we have */
      }
    } else if (await acquireLock()) {
      refreshSnapshot(apiKey).catch(() => {});
    }
  }

  const data = symbols.map((s) => tickFrom(snap, s));
  return NextResponse.json({
    date: snap?.date ?? null,
    updated: snap ? new Date(snap.at).toISOString() : null,
    source: snap ? 'polygon' : 'unavailable',
    data,
  });
}
