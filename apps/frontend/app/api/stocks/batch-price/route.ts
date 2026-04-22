import { NextRequest, NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Polygon's grouped-daily endpoint returns o/h/l/c for every US ticker in one
// call. We cache TWO consecutive trading days (latest + prior) so we can report
// daily change as (latest.c - prior.c) / prior.c — i.e. how much the stock
// moved since the previous market close. That costs two upstream calls per
// refresh window, well inside free-tier budget.
const FRESH_TTL_MS = 5 * 60_000;
const STALE_TTL_MS = 60 * 60_000;
const LOCK_TTL_SEC = 15;

type GroupedBar = { T: string; o: number; c: number; h: number; l: number; v: number };
type DayBars = { date: string; bars: Record<string, GroupedBar> };
type SnapshotEntry = { at: number; latest: DayBars; prior: DayBars };
type Tick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};

let memSnap: SnapshotEntry | null = null;
let inflight: Promise<SnapshotEntry | null> | null = null;

const SNAP_KEY = 'batch-price:grouped-2d';
const SNAP_LOCK = 'batch-price:lock-2d';

function stepBackTradingDay(d: Date): Date {
  const out = new Date(d);
  out.setUTCDate(out.getUTCDate() - 1);
  while (out.getUTCDay() === 0 || out.getUTCDay() === 6) {
    out.setUTCDate(out.getUTCDate() - 1);
  }
  return out;
}

function latestTradingDates(now = new Date()): { latest: string; prior: string } {
  // Polygon free tier posts EOD data after close, so "today" isn't reliable
  // during market hours. Walk back one trading day for `latest`, then another
  // for `prior`.
  let d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) {
    d.setUTCDate(d.getUTCDate() - 1);
  }
  d = stepBackTradingDay(d);
  const latest = d.toISOString().slice(0, 10);
  const prior = stepBackTradingDay(d).toISOString().slice(0, 10);
  return { latest, prior };
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

async function fetchGroupedDay(date: string, apiKey: string): Promise<DayBars | null> {
  const url = `https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/${date}?adjusted=true&apiKey=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error(`polygon grouped ${res.status}`);
  const json = await res.json();
  const results = (json?.results ?? []) as GroupedBar[];
  const bars: Record<string, GroupedBar> = {};
  for (const b of results) bars[b.T] = b;
  return { date, bars };
}

async function fetchSnapshot(apiKey: string): Promise<SnapshotEntry | null> {
  const { latest, prior } = latestTradingDates();
  const [latestBars, priorBars] = await Promise.all([
    fetchGroupedDay(latest, apiKey),
    fetchGroupedDay(prior, apiKey),
  ]);
  if (!latestBars || !priorBars) return null;
  return { at: Date.now(), latest: latestBars, prior: priorBars };
}

function refreshSnapshot(apiKey: string): Promise<SnapshotEntry | null> {
  if (inflight) return inflight;
  const p = (async () => {
    try {
      const snap = await fetchSnapshot(apiKey);
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
  const latest = snap?.latest.bars[symbol];
  const prior = snap?.prior.bars[symbol];
  const price = latest?.c ?? null;
  const previousClose = prior?.c ?? null;
  if (price === null || previousClose === null || previousClose === 0) {
    return { symbol, price, previousClose, change: null, changePct: null };
  }
  const change = price - previousClose;
  return { symbol, price, previousClose, change, changePct: change / previousClose };
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
        /* keep whatever we have */
      }
    } else if (await acquireLock()) {
      refreshSnapshot(apiKey).catch(() => {});
    }
  }

  const data = symbols.map((s) => tickFrom(snap, s));
  return NextResponse.json({
    latestDate: snap?.latest.date ?? null,
    priorDate: snap?.prior.date ?? null,
    updated: snap ? new Date(snap.at).toISOString() : null,
    source: snap ? 'polygon' : 'unavailable',
    data,
  });
}
