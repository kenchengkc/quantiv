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

import { currentRefreshWindow } from './marketHours';

export interface Env {
  REFRESH_URL: string;
  CRON_SECRET: string;
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
