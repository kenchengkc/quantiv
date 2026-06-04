import { NextRequest, NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import {
  etDateIso,
  isQuoteRefreshWindowET,
  lastCompletedTradingDayIso,
} from '@/lib/marketHours';
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
  // process.cwd() may be the repo root or apps/frontend depending on the
  // deployment, so try both (matches /api/cron/refresh-prices).
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public'),
    join(process.cwd(), 'public'),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return candidates[0];
}

function mondayIsoForEtDate(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  const day = d.getUTCDay();
  const delta = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

type CalEvent = { ticker: string; earningsDate: string; timing: string };

/** Every event across the calendar's active weeks (offsets -1..2), which is
 *  exactly the universe the calendar can display. Carries earnings_date+timing
 *  so the post-close pass can backfill realized moves, not just symbols. */
function calendarEvents(todayIso: string): CalEvent[] {
  const monday = new Date(`${mondayIsoForEtDate(todayIso)}T00:00:00Z`);
  const events: CalEvent[] = [];
  const seen = new Set<string>();
  for (const offset of [-1, 0, 1, 2]) {
    const d = new Date(monday);
    d.setUTCDate(d.getUTCDate() + 7 * offset);
    const iso = d.toISOString().slice(0, 10);
    const path = join(publicDir(), 'weeks', `${iso}.json`);
    if (!existsSync(path)) continue;
    try {
      const payload = JSON.parse(readFileSync(path, 'utf8')) as {
        events?: { ticker?: string; earnings_date?: string; timing?: string }[];
      };
      for (const e of payload.events ?? []) {
        if (!e.ticker) continue;
        const ticker = e.ticker.toUpperCase();
        const earningsDate = (e.earnings_date ?? '').slice(0, 10);
        const key = `${ticker}|${earningsDate}`;
        if (seen.has(key)) continue;
        seen.add(key);
        events.push({ ticker, earningsDate, timing: (e.timing ?? '').toLowerCase() });
      }
    } catch {
      /* skip unreadable week file */
    }
  }
  return events;
}

function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  return ab.length === bb.length && timingSafeEqual(ab, bb);
}

export async function GET(req: NextRequest) {
  // Prefer a dedicated BROAD_REFRESH_SECRET (so this endpoint doesn't depend on
  // the shared CRON_SECRET, which the Cloudflare worker also uses); still accept
  // CRON_SECRET so a manual curl with that token works.
  const accepted = [process.env.BROAD_REFRESH_SECRET, process.env.CRON_SECRET].filter(
    (s): s is string => Boolean(s),
  );
  if (accepted.length === 0) {
    return NextResponse.json(
      { error: 'BROAD_REFRESH_SECRET / CRON_SECRET not configured' },
      { status: 500 },
    );
  }
  const auth = req.headers.get('authorization') ?? '';
  if (!accepted.some((s) => safeEqual(auth, `Bearer ${s}`))) {
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
  const events = calendarEvents(todayIso);
  const universe = [...new Set(events.map((e) => e.ticker))];
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

  // Authoritative previous close = the close of the session *strictly before*
  // today (ET). That's the correct anchor for the live quote refresh path,
  // which writes today's Finnhub price but whose Finnhub `pc` field lags after
  // weekends/overnight. The cron reads prevclose:{SYM} and overrides `pc`.
  //   • today's grouped published (latest === today) → prior session
  //   • today still 403/unavailable (latest === yesterday) → latest session
  const prevCloseSource = isToday ? sessions.prior : sessions.latest;

  // Only overwrite the full quote:{SYM} when grouped-daily's latest bar is
  // actually the most recent completed session. On a weekday evening today's
  // bar isn't published yet (403), so grouped's "latest" is the PRIOR day —
  // writing that would regress the fresher Finnhub close already in cache. The
  // prevclose:{SYM} reference is always safe to write (it's the prior close by
  // definition).
  const groupedIsCurrent = sessions.latest.date === lastCompletedTradingDayIso();

  const pipeline = redis.pipeline();
  let written = 0;
  let prevcloseWritten = 0;
  for (const symbol of universe) {
    const latest = sessions.latest.closes.get(symbol);
    if (!latest) continue;
    if (groupedIsCurrent) {
      const prior = sessions.prior?.closes.get(symbol) ?? null;
      const entry: CachedQuote = {
        at: now,
        tick: { price: latest.close, previousClose: prior ? prior.close : null },
        source: 'polygon_grouped',
        session,
      };
      // Store the object directly (Upstash serializes) to match the other
      // quote:{SYM} writers; batch-price's mget deserializes it back. 48h TTL
      // bridges a missed cron without the cache going empty.
      pipeline.set(`quote:${symbol}`, entry, { ex: 172_800 });
      written++;
    }

    const prevClose = prevCloseSource?.closes.get(symbol)?.close ?? null;
    if (prevClose != null) {
      // 3-day TTL survives a weekend so Friday's close stays the authoritative
      // previous close through Monday's session until it is rewritten.
      pipeline.set(`prevclose:${symbol}`, prevClose, { ex: 259_200 });
      prevcloseWritten++;
    }
  }

  // ── Realized-move backfill ───────────────────────────────────────────────
  // The static realized_move_pct in weeks/*.json only refreshes in the 7 AM ET
  // nightly build, so between a reaction's close and that build (≈15 h) the
  // calendar shows nothing. Once grouped-daily's latest bar is the last
  // completed session, latest/prior closes ARE the earnings-reaction closes for
  // events that just resolved — write them as realized:{SYM} so the calendar can
  // show REALIZED immediately instead of waiting for the build.
  //   • BMO reporting on the latest session: prior close → latest close
  //   • AMC reporting on the prior session:  prior close → latest close
  // (AMC's report-day close is `prior`, its reaction close is `latest`.)
  let realizedWritten = 0;
  if (groupedIsCurrent && sessions.prior) {
    const latestDate = sessions.latest.date;
    const priorDate = sessions.prior.date;
    for (const ev of events) {
      const isBmo = ev.timing === 'bmo' || ev.timing.includes('before');
      const isAmc = ev.timing === 'amc' || ev.timing.includes('after');
      const reactsOnLatest =
        (isBmo && ev.earningsDate === latestDate) ||
        (isAmc && ev.earningsDate === priorDate);
      if (!reactsOnLatest) continue;
      const post = sessions.latest.closes.get(ev.ticker)?.close ?? null;
      const pre = sessions.prior.closes.get(ev.ticker)?.close ?? null;
      if (post == null || pre == null || pre <= 0) continue;
      const realized = post / pre - 1;
      // Event-keyed payload: the consumer only trusts it when `date` matches the
      // event's earnings_date, so a stale key can never paint the wrong quarter.
      // 4-day TTL bridges a long weekend until the nightly build bakes it in.
      pipeline.set(
        `realized:${ev.ticker}`,
        { date: ev.earningsDate, pct: realized },
        { ex: 345_600 },
      );
      realizedWritten++;
    }
  }

  if (written > 0 || prevcloseWritten > 0 || realizedWritten > 0) await pipeline.exec();

  return NextResponse.json({
    universe: universe.length,
    quotesWritten: written,
    groupedIsCurrent,
    prevcloseWritten,
    realizedWritten,
    prevcloseSession: prevCloseSource?.date ?? null,
    latestSession: sessions.latest.date,
    priorSession: sessions.prior?.date ?? null,
    session,
    source: 'polygon_grouped',
  });
}
