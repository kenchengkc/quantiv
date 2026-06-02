import { NextRequest, NextResponse } from 'next/server';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { Redis } from '@upstash/redis';
import { neon } from '@neondatabase/serverless';
import sp500Constituents from '../../../../../../lib/data/sp500-constituents.json';
import {
  isQuoteRefreshWindowET,
  isPremarketWindowET,
  isAfterhoursWindowET,
  etDateIso,
  type RefreshWindowKind,
} from '@/lib/marketHours';
import { fetchExtendedHoursSnapshot } from '@/lib/alpaca';

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
// 60s is the Hobby Fluid Compute ceiling. With 45 symbols × ~1.091s pacing
// (55/min cap) starts span ~49s; including per-call latency + Redis writes,
// expect ~55s wall clock — safe margin under the 60s timeout and 5 calls/min
// headroom below Finnhub's 60/min limit. Worker fires this every 1 min so
// total throughput is ~45 symbols/min (rotation bottlenecked by cron cadence,
// not within-batch pacing).
export const maxDuration = 60;

const BATCH_SIZE = 45;
// 55/min stays under the 60/min Finnhub free-tier cap with a 5-call safety
// margin for occasional retries / clock-skew at minute boundaries. Pushing
// higher (e.g. 58) leaves only 2 calls/min of headroom; one stalled call +
// the next fire on schedule can briefly exceed the rolling-minute window and
// trigger a 429.
const RATE_LIMIT_PER_MIN = 55;
// 7 days — covers Fri close → Mon open weekends, 3-day holiday weekends,
// and any cron downtime. The cron rewrites every ~30 min during market
// hours, so active entries are nowhere near this limit; it's purely a
// safety net to keep last-close prices visible when no fresh data is
// being written.
const STALE_TTL_S = 7 * 24 * 60 * 60;
const CURSOR_KEY = 'quote:cursor';

type Tick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};

/** Cache envelope stored in Upstash under `quote:SYMBOL`. The two new
 *  fields (source, session) are additive — older readers that destructure
 *  `{ at, tick }` keep working. batch-price reads them to label the
 *  response with the actual upstream provider + ET session the tick
 *  came from, instead of always reporting 'finnhub'. */
type CachedQuote = {
  at: number;
  tick: Tick;
  source?: 'finnhub' | 'alpaca_iex';
  session?: 'premarket' | 'regular' | 'afterhours';
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

function redisOrError(): { redis: Redis } | { response: NextResponse } {
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    return {
      response: NextResponse.json(
        { error: 'Upstash Redis env missing' },
        { status: 500 },
      ),
    };
  }
  return { redis: new Redis({ url, token }) };
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

/** Today's reporters filtered to a specific session. `todayIso` must be
 *  the ET date (use etDateIso(), not server local). The week file is
 *  indexed by ET Monday, computed from the same ET date here. */
function loadTodaysReporters(
  todayIso: string,
  session: 'bmo' | 'amc',
): string[] {
  const monIso = mondayIsoForEtDate(todayIso);
  const candidatePaths = [
    join(publicDir(), 'weeks', `${monIso}.json`),
    // Fallback for any future per-day file layout.
    join(publicDir(), 'weeks', `${todayIso}.json`),
  ];
  for (const p of candidatePaths) {
    if (!existsSync(p)) continue;
    try {
      const payload = JSON.parse(readFileSync(p, 'utf8'));
      const events = (payload.events as {
        ticker: string;
        earnings_date?: string;
        timing?: string;
      }[] | undefined) ?? [];
      return Array.from(new Set(
        events
          .filter((e) => {
            if (!e.ticker) return false;
            if ((e.earnings_date ?? '').slice(0, 10) !== todayIso) return false;
            const t = (e.timing ?? '').toLowerCase();
            if (session === 'bmo') {
              return t === 'bmo' || t.includes('before');
            }
            return t === 'amc' || t.includes('after');
          })
          .map((e) => e.ticker.toUpperCase()),
      ));
    } catch {
      /* fall through to next candidate */
    }
  }
  return [];
}

function mondayIsoForEtDate(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  const day = d.getUTCDay();
  const delta = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
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
  const weekAfterNextMon = new Date(thisMon); weekAfterNextMon.setDate(thisMon.getDate() + 14);

  const sp500 = (sp500Constituents as { symbol: string }[]).map((c) => c.symbol);

  // Priority tiers — first one wins on dedup. Week +2 sits above SP500 so
  // earnings the user can see in the calendar refresh before the generic
  // SP500 list. Cycle time grows ~22 min → ~30 min as a result.
  const tiers = [
    await loadWatchlistSymbols(),
    loadWeekFile(`${isoDay(thisMon)}.json`),
    loadWeekFile(`${isoDay(nextMon)}.json`),
    loadWeekFile(`${isoDay(weekAfterNextMon)}.json`),
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

function regularPollingHandledByRailway(): boolean {
  return (
    process.env.QUOTE_REFRESH_PROVIDER === 'railway' ||
    process.env.DISABLE_VERCEL_REGULAR_QUOTE_REFRESH === '1'
  );
}

// Constant-time string compare to avoid timing-leak side channels.
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function GET(req: NextRequest) {
  const required = process.env.CRON_SECRET;
  if (!required) {
    return NextResponse.json({ error: 'CRON_SECRET not configured' }, { status: 500 });
  }
  // Bearer only — query secrets leak through proxy logs, browser history,
  // and access logs. The Cloudflare Worker already sets the Authorization
  // header; the `?secret=` fallback was a temporary debugging affordance.
  const auth = req.headers.get('authorization') ?? '';
  const expected = `Bearer ${required}`;
  if (!safeEqual(auth, expected)) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const reqUrl = new URL(req.url);

  // Three refresh modes:
  //   • regular     — Finnhub rotating cursor (09:25–16:45 ET)
  //   • premarket   — Alpaca extended-hours snapshot for today's BMO reporters
  //   • afterhours  — Alpaca extended-hours snapshot for today's AMC reporters
  //
  // Worker passes `?window=...` based on its own ET-window check. `?force=1`
  // overrides the market-hours guard for manual back-fills (force defaults to
  // the regular flow if no window is specified).
  const windowRaw = reqUrl.searchParams.get('window');
  const windowParam =
    windowRaw === 'premarket' || windowRaw === 'regular' || windowRaw === 'afterhours'
      ? windowRaw
      : null;
  if (windowRaw && !windowParam) {
    return NextResponse.json({ error: 'invalid window' }, { status: 400 });
  }
  const force = reqUrl.searchParams.get('force') === '1';
  const kind: RefreshWindowKind = windowParam ?? 'regular';

  const inWindow =
    kind === 'premarket' ? isPremarketWindowET() :
    kind === 'afterhours' ? isAfterhoursWindowET() :
    isQuoteRefreshWindowET();
  if (!force && !inWindow) {
    return NextResponse.json({
      universe: 0,
      fetched: 0,
      window: kind,
      skipped: 'market_closed',
    });
  }

  // Feature flag for the Alpaca extended-hours rollout. While off, the
  // route still classifies the window and returns 200 so the Worker stays
  // healthy, but no upstream call is made. Existing regular-hours flow is
  // unaffected by this flag.
  const extendedEnabled = process.env.ENABLE_ALPACA_EXTENDED_QUOTES === '1';
  if ((kind === 'premarket' || kind === 'afterhours') && !extendedEnabled) {
    if (kind === 'afterhours' && isQuoteRefreshWindowET()) {
      if (regularPollingHandledByRailway()) {
        return NextResponse.json({
          universe: 0,
          fetched: 0,
          window: kind,
          skipped: 'extended_quotes_disabled',
          regularSettle: {
            skipped: 'regular_quotes_handled_by_railway',
          },
        });
      }
      const ready = redisOrError();
      if ('response' in ready) return ready.response;
      const regularSettle = await runRegularHours(ready.redis);
      const regularBody = await responseJson(regularSettle);
      return NextResponse.json(
        {
          window: kind,
          universe: 0,
          fetched: 0,
          skipped: 'extended_quotes_disabled',
          regularSettle: {
            status: regularSettle.status,
            body: regularBody,
          },
        },
        { status: regularSettle.status },
      );
    }
    return NextResponse.json({
      universe: 0,
      fetched: 0,
      window: kind,
      skipped: 'extended_quotes_disabled',
    });
  }

  const ready = redisOrError();
  if ('response' in ready) return ready.response;
  const { redis } = ready;

  if (kind === 'premarket' || kind === 'afterhours') {
    if (kind === 'afterhours' && isQuoteRefreshWindowET()) {
      if (regularPollingHandledByRailway()) {
        return runExtendedHours(kind, redis);
      }
      // Preserve the original full-universe Finnhub settle pass during
      // 16:00-16:45 ET, but exclude today's AMC reporters so fresh Alpaca
      // extended-hours writes cannot be overwritten by stale Finnhub ticks.
      const amcReporters = new Set(loadTodaysReporters(etDateIso(), 'amc'));
      const regularSettlePromise = runRegularHours(redis, amcReporters);
      const extended = await runExtendedHours(kind, redis);
      const regularSettle = await regularSettlePromise;
      const extendedBody = await responseJson(extended);
      const regularBody = await responseJson(regularSettle);
      return NextResponse.json(
        {
          ...(isRecord(extendedBody) ? extendedBody : { extended: extendedBody }),
          regularSettle: {
            status: regularSettle.status,
            body: regularBody,
          },
        },
        { status: extended.status },
      );
    }
    return runExtendedHours(kind, redis);
  }
  if (regularPollingHandledByRailway()) {
    return NextResponse.json({
      window: 'regular',
      universe: 0,
      fetched: 0,
      skipped: 'regular_quotes_handled_by_railway',
    });
  }
  return runRegularHours(redis);
}

/** Regular-hours flow — Finnhub rotating cursor across the full universe.
 *  This is the original behavior, refactored out so the new GET handler
 *  can branch cleanly. Unchanged semantics. */
async function runRegularHours(
  redis: Redis,
  excludeSymbols: ReadonlySet<string> = new Set(),
): Promise<NextResponse> {
  const apiKey = process.env.FINNHUB_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: 'FINNHUB_API_KEY missing' }, { status: 500 });
  }

  const symbols = (await buildSymbolList()).filter((s) => !excludeSymbols.has(s.toUpperCase()));
  if (symbols.length === 0) {
    return NextResponse.json({ universe: 0, fetched: 0, message: 'no symbols to refresh' });
  }

  const raw = await redis.get(CURSOR_KEY);
  const cursor = Number.isFinite(Number(raw)) ? Number(raw) % symbols.length : 0;
  const end = cursor + BATCH_SIZE;
  const batch: string[] = [];
  for (let i = cursor; i < end; i++) batch.push(symbols[i % symbols.length]);
  const nextCursor = end % symbols.length;

  // Authoritative previous closes (Polygon grouped-daily, written by
  // /api/cron/refresh-broad as prevclose:{SYM}) override Finnhub's `pc`, which
  // lags after weekends/overnight — that lag inflated reaction % on the
  // calendar (e.g. an 8% move shown as 15%). Missing keys fall back to
  // Finnhub's pc, so this is strictly additive.
  const prevcloseMap = new Map<string, number>();
  try {
    const vals = (await redis.mget<(number | null)[]>(
      ...batch.map((s) => `prevclose:${s}`),
    )) ?? [];
    batch.forEach((s, i) => {
      const v = vals[i];
      if (typeof v === 'number' && v > 0) prevcloseMap.set(s, v);
    });
  } catch {
    // Best-effort: on a Redis hiccup, just use Finnhub's pc for this batch.
  }

  const spacingMs = Math.ceil(60_000 / RATE_LIMIT_PER_MIN);
  let ok2 = 0;
  let fail = 0;
  const start = Date.now();
  for (const symbol of batch) {
    const tickStart = Date.now();
    const tick = await fetchQuote(symbol, apiKey);
    if (tick) {
      try {
        const authoritativePrev = prevcloseMap.get(symbol);
        const entry: CachedQuote = {
          at: Date.now(),
          tick:
            authoritativePrev != null
              ? { ...tick, previousClose: authoritativePrev }
              : tick,
          source: 'finnhub',
          session: 'regular',
        };
        await redis.set(`quote:${symbol}`, entry, { ex: STALE_TTL_S });
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
    window: 'regular',
    universe: symbols.length,
    batchSize: batch.length,
    cursor,
    nextCursor,
    fetched: ok2,
    failed: fail,
    durationMs: Date.now() - start,
  });
}

async function responseJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Extended-hours flow — Alpaca snapshot for *only* today's BMO or AMC
 *  reporters. No rotating cursor: the universe is small (~10-40 tickers
 *  per session) and the snapshot endpoint batches them in one HTTP call,
 *  so each window finishes in well under a second. */
async function runExtendedHours(
  kind: 'premarket' | 'afterhours',
  redis: Redis,
): Promise<NextResponse> {
  const session = kind === 'premarket' ? 'bmo' : 'amc';
  // ET date — NOT server local. During EST after-hours, 19:30 ET Monday
  // is 00:30 UTC Tuesday, so a server-local-date filter would look for
  // Tuesday reporters instead of Monday AMC reporters.
  const todayIso = etDateIso();
  const symbols = loadTodaysReporters(todayIso, session);

  if (symbols.length === 0) {
    return NextResponse.json({
      window: kind,
      universe: 0,
      fetched: 0,
      message: 'no reporters today',
      todayEt: todayIso,
    });
  }

  const start = Date.now();
  let fetched = 0;
  let fail = 0;
  try {
    const snap = await fetchExtendedHoursSnapshot(symbols);
    for (const symbol of symbols) {
      const tick = snap.get(symbol);
      if (!tick) {
        fail++;
        continue;
      }
      try {
        const entry: CachedQuote = {
          at: Date.now(),
          tick,
          source: 'alpaca_iex',
          session: kind,
        };
        await redis.set(`quote:${symbol}`, entry, { ex: STALE_TTL_S });
        fetched++;
      } catch {
        fail++;
      }
    }
  } catch (err) {
    return NextResponse.json(
      { window: kind, error: (err as Error).message },
      { status: 502 },
    );
  }

  return NextResponse.json({
    window: kind,
    universe: symbols.length,
    fetched,
    failed: fail,
    durationMs: Date.now() - start,
    symbols,
    todayEt: todayIso,
  });
}
