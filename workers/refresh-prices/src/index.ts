// Cloudflare Worker: trigger Vercel's price refresh route on a real 5-min
// cadence. Workers cron is reliable (sub-minute precision, no throttling)
// unlike GitHub Actions schedules which coalesce to ~hourly on the free tier.
//
// All actual work runs on Vercel — this file is just the trigger.

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
    // Don't await the full Vercel response — the route can take up to 5 min
    // to pace through 300 symbols. Worker scheduled events are billed per
    // CPU-ms, not wall-clock, so kicking off the request and detaching is
    // both faster and cheaper than holding the connection open.
    ctx.waitUntil(
      (async () => {
        try {
          const res = await fetch(env.REFRESH_URL, {
            method: 'GET',
            headers: { Authorization: `Bearer ${env.CRON_SECRET}` },
            // Worker-side timeout safety net. Vercel maxDuration caps it to 300s.
            signal: AbortSignal.timeout(310_000),
          });
          console.log(`refresh response: ${res.status}`);
          if (!res.ok) {
            const text = await res.text().catch(() => '');
            console.error(`non-ok response body: ${text.slice(0, 500)}`);
          }
        } catch (err) {
          console.error('refresh fetch failed:', err);
        }
      })(),
    );
  },
};
