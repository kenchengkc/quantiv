// Cloudflare Worker: trigger Vercel's price refresh route during one of
// three ET windows — premarket, regular, or afterhours — based on the
// minute the cron fires. Each window has different upstream behavior on
// the Vercel side (Finnhub rotating cursor for regular, Alpaca snapshot
// of today's BMO/AMC reporters for the extended-hours windows), which
// we communicate via a `?window=...` query param.
//
// The cron pattern in wrangler.toml fires only during a loose UTC
// superset of the union of the three windows; the precise ET +
// holiday check below drops edge minutes so Vercel is never woken on
// a minute that would just short-circuit back out.

export interface Env {
  REFRESH_URL: string;
  CRON_SECRET: string;
}

// ─── Market-hours guard ────────────────────────────────────────────────
// Mirrors apps/frontend/lib/marketHours.ts. The Worker can't import from
// the Next app (separate deploy target), so the constants and holiday
// set are duplicated here — keep both in sync when updating NYSE
// holidays or window bounds.
//
// DST is handled by `Intl.DateTimeFormat({ timeZone: 'America/New_York' })`
// below — it consults the IANA tz database at runtime, so the precise
// EDT↔EST transition dates (US DST: 2nd Sun of Mar → 1st Sun of Nov, so
// 2026-03-08, 2026-11-01, 2027-03-14, 2027-11-07, 2028-03-12, 2028-11-05)
// don't need to be hardcoded here.

const PREMARKET_OPEN_MIN   = 8  * 60;       // 08:00 ET (IEX pre-market start)
const PREMARKET_CLOSE_MIN  = 9  * 60 + 24;  // 09:24 ET
const QUOTE_REFRESH_OPEN_MIN  = 9  * 60 + 25;  // 09:25 ET
const QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45;  // 16:45 ET
const AFTERHOURS_OPEN_MIN  = 16 * 60;       // 16:00 ET
const AFTERHOURS_CLOSE_MIN = 17 * 60;       // 17:00 ET (IEX post-market end)

type WindowKind = 'premarket' | 'regular' | 'afterhours';

// Keep aligned with apps/frontend/lib/marketHours.ts NYSE_HOLIDAYS.
const NYSE_HOLIDAYS: ReadonlySet<string> = new Set([
  // 2026
  '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25',
  '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25',
  // 2027
  '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31',
  '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24',
  // 2028
  '2028-01-17', '2028-02-21', '2028-04-14', '2028-05-29', '2028-06-19',
  '2028-07-04', '2028-09-04', '2028-11-23', '2028-12-25',
]);

function nowEtParts(d: Date): { weekday: string; minutes: number; isoDate: string } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const m: Record<string, string> = {};
  for (const p of parts) m[p.type] = p.value;
  return {
    weekday: m.weekday ?? 'Sun',
    minutes: ((parseInt(m.hour ?? '0') % 24) * 60) + parseInt(m.minute ?? '0'),
    isoDate: `${m.year ?? '0000'}-${m.month ?? '01'}-${m.day ?? '01'}`,
  };
}

function currentRefreshWindow(d: Date): WindowKind | null {
  const { weekday, minutes, isoDate } = nowEtParts(d);
  if (weekday === 'Sat' || weekday === 'Sun') return null;
  if (NYSE_HOLIDAYS.has(isoDate)) return null;
  if (minutes >= PREMARKET_OPEN_MIN     && minutes <= PREMARKET_CLOSE_MIN)     return 'premarket';
  // After-hours takes precedence from the 16:00 bell so AMC reporters
  // get Alpaca extended-hours quotes during the most important window.
  if (minutes >= AFTERHOURS_OPEN_MIN    && minutes <= AFTERHOURS_CLOSE_MIN)    return 'afterhours';
  if (minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= QUOTE_REFRESH_CLOSE_MIN) return 'regular';
  return null;
}

export default {
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    if (!env.REFRESH_URL || !env.CRON_SECRET) {
      console.error('REFRESH_URL or CRON_SECRET missing');
      return;
    }

    // Use the scheduled time (the exact moment the cron fired) rather
    // than `new Date()`. The cron platform passes `scheduledTime` as a
    // ms-since-epoch integer; this is deterministic and avoids edge
    // cases where the Worker boot delay would otherwise nudge a
    // 13:25 UTC fire across the 13:24 boundary on the wall clock.
    const firedAt = new Date(event.scheduledTime ?? Date.now());
    const kind = currentRefreshWindow(firedAt);
    if (!kind) {
      // Off-window edge minute (DST seam, NYSE holiday, etc.). Intentionally
      // quiet — would log on every off-window fire (~10-30/day). Uncomment
      // if debugging.
      // console.log('outside refresh window — skipping');
      return;
    }

    // Tack the window kind onto the URL so the Vercel route picks the
    // right upstream (Finnhub rotating cursor vs Alpaca snapshot).
    const target = new URL(env.REFRESH_URL);
    target.searchParams.set('window', kind);

    // Don't await the full Vercel response — the regular-hours route can
    // take up to 60s. Worker scheduled events are billed per CPU-ms, not
    // wall-clock, so kicking off the request and detaching is cheaper.
    ctx.waitUntil(
      (async () => {
        try {
          const res = await fetch(target.toString(), {
            method: 'GET',
            headers: { Authorization: `Bearer ${env.CRON_SECRET}` },
            // Vercel maxDuration is 60s; 70s gives slack without lingering.
            signal: AbortSignal.timeout(70_000),
          });
          console.log(`refresh ${kind}: ${res.status}`);
          if (!res.ok) {
            const text = await res.text().catch(() => '');
            console.error(`non-ok response body: ${text.slice(0, 500)}`);
          }
        } catch (err) {
          console.error(`refresh ${kind} fetch failed:`, err);
        }
      })(),
    );
  },
};
