import { NextRequest, NextResponse } from 'next/server';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { Redis } from '@upstash/redis';
import { neon } from '@neondatabase/serverless';
import sp500Constituents from '../../../../../../lib/data/sp500-constituents.json';

// Cron-driven Finnhub price refresher. Triggered every 5 min by an external
// scheduler (Cloudflare Worker). Walks a rotating cursor through a priority-
// ordered symbol list and writes per-ticker quote entries to Upstash Redis.
//
// Auth: must send `Authorization: Bearer ${CRON_SECRET}` (or
// `?secret=${CRON_SECRET}` query param). Without it → 401.
//
// Why a Vercel route instead of doing all this inside the Worker itself:
//   - We get 300s function timeout on Fluid Compute, plenty for 60/min × 5min.
//   - Runtime already has @upstash/redis, @neondatabase/serverless, and
//     filesystem access to public/weeks/*.json (bundled with the app).
//   - The Worker stays trivial — just a scheduled URL kick.

export const dynamic = 'force-dynamic';
// 60s is the Hobby Fluid Compute ceiling. With 40 symbols × ~1.2s pacing we
// finish in ~48s — comfortably under. Worker fires this every 1 min so total
// throughput is ~50/min, matching Finnhub's rate limit.
export const maxDuration = 60;

const BATCH_SIZE = 40;
const RATE_LIMIT_PER_MIN = 50;     // 60/min Finnhub limit, leave 10/min headroom
const STALE_TTL_S = 24 * 60 * 60;  // 24h — surfaces last-known price for symbols that briefly fall off rotation
const CURSOR_KEY = 'quote:cursor';

type Tick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};

// In a Vercel deployment the public dir is at /var/task/apps/frontend/public.
// During `next dev` it's relative to cwd. Both layouts have the file at
// `apps/frontend/public/weeks/*.json` from the repo root.
function publicDir(): string {
  // process.cwd() in a Vercel function is the project root by default.
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public'),
    join(process.cwd(), 'public'),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return candidates[0];
}

function loadWeekFile(filename: string): string[] {
  const path = join(publicDir(), 'weeks', filename);
  if (!existsSync(path)) return [];
  try {
    const payload = JSON.parse(readFileSync(path, 'utf8'));
    return ((payload.events as { ticker: string }[] | undefined) ?? [])
      .map((e) => e.ticker)
      .filter(Boolean);
  } catch {
    return [];
  }
}

function mondayOf(d: Date): Date {
  const out = new Date(d);
  const day = out.getDay();
  const delta = day === 0 ? -6 : 1 - day;
  out.setDate(out.getDate() + delta);
  out.setHours(0, 0, 0, 0);
  return out;
}

function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

async function loadWatchlistSymbols(): Promise<string[]> {
  const url = process.env.DATABASE_URL;
  if (!url) return [];
  try {
    const sql = neon(url);
    const rows = (await sql`SELECT DISTINCT symbol FROM watchlist`) as { symbol: string }[];
    return rows.map((r) => r.symbol);
  } catch {
    return [];
  }
}

async function buildSymbolList(): Promise<string[]> {
  const thisMon = mondayOf(new Date());
  const lastMon = new Date(thisMon); lastMon.setDate(thisMon.getDate() - 7);
  const nextMon = new Date(thisMon); nextMon.setDate(thisMon.getDate() + 7);

  const sp500 = (sp500Constituents as { symbol: string }[]).map((c) => c.symbol);

  const tiers = [
    await loadWatchlistSymbols(),
    loadWeekFile(`${isoDay(thisMon)}.json`),
    loadWeekFile(`${isoDay(nextMon)}.json`),
    sp500,
    loadWeekFile(`${isoDay(lastMon)}.json`),
  ];

  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const tier of tiers) {
    for (const s of tier) {
      if (!seen.has(s)) {
        seen.add(s);
        ordered.push(s);
      }
    }
  }
  return ordered;
}

async function fetchQuote(symbol: string, apiKey: string): Promise<Tick | null> {
  const url = `https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(symbol)}&token=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) return null;
  const json = (await res.json()) as { c?: number; pc?: number; d?: number; dp?: number };
  const price = typeof json.c === 'number' && json.c > 0 ? json.c : null;
  if (price === null) return null;
  return {
    symbol,
    price,
    previousClose: typeof json.pc === 'number' && json.pc > 0 ? json.pc : null,
    change: typeof json.d === 'number' ? json.d : null,
    changePct: typeof json.dp === 'number' ? json.dp / 100 : null,
  };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export async function GET(req: NextRequest) {
  const required = process.env.CRON_SECRET;
  if (!required) {
    return NextResponse.json({ error: 'CRON_SECRET not configured' }, { status: 500 });
  }
  const auth = req.headers.get('authorization') ?? '';
  const param = new URL(req.url).searchParams.get('secret');
  const ok = auth === `Bearer ${required}` || param === required;
  if (!ok) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });

  const apiKey = process.env.FINNHUB_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: 'FINNHUB_API_KEY missing' }, { status: 500 });
  }
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    return NextResponse.json({ error: 'Upstash Redis env missing' }, { status: 500 });
  }
  const redis = new Redis({ url, token });

  const symbols = await buildSymbolList();
  if (symbols.length === 0) {
    return NextResponse.json({ universe: 0, fetched: 0, message: 'no symbols to refresh' });
  }

  const raw = await redis.get(CURSOR_KEY);
  const cursor = Number.isFinite(Number(raw)) ? Number(raw) % symbols.length : 0;
  const end = cursor + BATCH_SIZE;
  const batch: string[] = [];
  for (let i = cursor; i < end; i++) batch.push(symbols[i % symbols.length]);
  const nextCursor = end % symbols.length;

  const spacingMs = Math.ceil(60_000 / RATE_LIMIT_PER_MIN);
  let ok2 = 0;
  let fail = 0;
  const start = Date.now();
  for (const symbol of batch) {
    const tickStart = Date.now();
    const tick = await fetchQuote(symbol, apiKey);
    if (tick) {
      try {
        await redis.set(`quote:${symbol}`, { at: Date.now(), tick }, { ex: STALE_TTL_S });
        ok2++;
      } catch {
        fail++;
      }
    } else {
      fail++;
    }
    const elapsed = Date.now() - tickStart;
    if (elapsed < spacingMs) await sleep(spacingMs - elapsed);
  }
  await redis.set(CURSOR_KEY, nextCursor);

  return NextResponse.json({
    universe: symbols.length,
    batchSize: batch.length,
    cursor,
    nextCursor,
    fetched: ok2,
    failed: fail,
    durationMs: Date.now() - start,
  });
}
