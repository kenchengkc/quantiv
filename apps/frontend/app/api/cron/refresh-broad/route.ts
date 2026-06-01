import { NextRequest, NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import { etDateIso, isQuoteRefreshWindowET } from '@/lib/marketHours';
import { fetchLatestTwoSessions } from '@/lib/polygon';

// Broad, whole-universe quote refresh from Polygon/Massive grouped-daily
// aggregates: ONE upstream call covers ~12k tickers, so the calendar's full
// reporter set is refreshed in a single request instead of waiting out
// Finnhub's 30+ min single-symbol cursor. Free-tier data is 15-min delayed
// (today's bar) or the last close once a session ends — labelled accordingly so
// the UI never presents it as a live tick.
//
// This route is intentionally independent of /api/cron/refresh-prices: it only
// writes quote:{SYM} cache entries (same shape) for symbols that the calendar
// shows, and never touches the Finnhub/Alpaca code paths. Wire it to its own
// Cloudflare cron trigger (e.g. every few minutes, or once post-close).

export const dynamic = 'force-dynamic';
export const revalidate = 0;
export const maxDuration = 60;

type Tick = { price: number | null; previousClose: number | null };
type CachedQuote = {
  at: number;
  tick: Tick;
  source?: 'polygon_grouped';
  session?: 'delayed' | 'closed';
};

function publicDir(): string {
  return join(process.cwd(), 'apps', 'frontend', 'public');
}

function mondayIsoForEtDate(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  const day = d.getUTCDay();
  const delta = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

/** Distinct tickers across the calendar's active weeks (offsets -1..2), which
 *  is exactly the universe the calendar can display. */
function calendarUniverse(todayIso: string): string[] {
  const monday = new Date(`${mondayIsoForEtDate(todayIso)}T00:00:00Z`);
  const symbols = new Set<string>();
  for (const offset of [-1, 0, 1, 2]) {
    const d = new Date(monday);
    d.setUTCDate(d.getUTCDate() + 7 * offset);
    const iso = d.toISOString().slice(0, 10);
    const path = join(publicDir(), 'weeks', `${iso}.json`);
    if (!existsSync(path)) continue;
    try {
      const payload = JSON.parse(readFileSync(path, 'utf8')) as {
        events?: { ticker?: string }[];
      };
      for (const e of payload.events ?? []) {
        if (e.ticker) symbols.add(e.ticker.toUpperCase());
      }
    } catch {
      /* skip unreadable week file */
    }
  }
  return [...symbols];
}

function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  return ab.length === bb.length && timingSafeEqual(ab, bb);
}

export async function GET(req: NextRequest) {
  const required = process.env.CRON_SECRET;
  if (!required) {
    return NextResponse.json({ error: 'CRON_SECRET not configured' }, { status: 500 });
  }
  if (!safeEqual(req.headers.get('authorization') ?? '', `Bearer ${required}`)) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  // Only refresh while the market is closed. During the live quote-refresh
  // window the Finnhub/Alpaca path writes fresh quotes for priority reporters;
  // grouped-daily is 15-min delayed, so writing it then would clobber those
  // live ticks. `?force=1` overrides for manual backfills.
  const force = new URL(req.url).searchParams.get('force') === '1';
  if (!force && isQuoteRefreshWindowET()) {
    return NextResponse.json({ universe: 0, written: 0, skipped: 'market_open' });
  }

  const apiKey = process.env.POLYGON_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: 'POLYGON_API_KEY not configured' }, { status: 500 });
  }
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    return NextResponse.json({ error: 'Upstash Redis env missing' }, { status: 500 });
  }
  const redis = new Redis({ url, token });

  const todayIso = etDateIso();
  const universe = calendarUniverse(todayIso);
  if (universe.length === 0) {
    return NextResponse.json({ universe: 0, written: 0, skipped: 'no_active_week_reporters' });
  }

  const sessions = await fetchLatestTwoSessions(apiKey, todayIso);
  if (!sessions) {
    return NextResponse.json({ universe: universe.length, written: 0, skipped: 'no_polygon_data' });
  }

  // Today's session counts as delayed (intraday bar so far); an older latest
  // session means the market is closed and we are showing the last close.
  const isToday = sessions.latest.date === todayIso;
  const session: CachedQuote['session'] = isToday ? 'delayed' : 'closed';
  const now = Date.now();

  const pipeline = redis.pipeline();
  let written = 0;
  for (const symbol of universe) {
    const latest = sessions.latest.closes.get(symbol);
    if (!latest) continue;
    const prior = sessions.prior?.closes.get(symbol) ?? null;
    const entry: CachedQuote = {
      at: now,
      tick: { price: latest.close, previousClose: prior ? prior.close : null },
      source: 'polygon_grouped',
      session,
    };
    // Store the object directly (Upstash serializes) to match the other
    // quote:{SYM} writers; batch-price's mget deserializes it back.
    // 48h TTL bridges a missed cron (GitHub Actions schedules can be delayed
    // or skipped) without the cache going empty between closed-market runs.
    pipeline.set(`quote:${symbol}`, entry, { ex: 172_800 });
    written++;
  }
  if (written > 0) await pipeline.exec();

  return NextResponse.json({
    universe: universe.length,
    written,
    latestSession: sessions.latest.date,
    priorSession: sessions.prior?.date ?? null,
    session,
    source: 'polygon_grouped',
  });
}
