const path = require('path');
const fs = require('fs');

// Load env from repo-level config/.env.* (local dev only; on Vercel env
// vars are injected via the dashboard so this file may not exist).
try {
  const dotenv = require('dotenv');
  const repoRoot = path.resolve(__dirname, '..', '..');
  const envFile = (process.env.NODE_ENV === 'production' || process.env.ENVIRONMENT === 'production')
    ? '.env.production'
    : '.env.local';
  const envPath = path.join(repoRoot, 'config', envFile);
  if (fs.existsSync(envPath)) {
    dotenv.config({ path: envPath, quiet: true });
  }
  // Local dev/test envs often keep Clerk dev-instance keys under E2E_*
  // names so they are not confused with production Vercel variables.
  // Clerk's Next runtime still expects the standard names.
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.E2E_CLERK_PUBLISHABLE_KEY) {
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = process.env.E2E_CLERK_PUBLISHABLE_KEY;
  }
  if (!process.env.CLERK_SECRET_KEY && process.env.E2E_CLERK_SECRET_KEY) {
    process.env.CLERK_SECRET_KEY = process.env.E2E_CLERK_SECRET_KEY;
  }
} catch (_) {
  // dotenv not available in production build — env vars come from Vercel
}

// Logo.dev publishable key — MUST stay OUTSIDE the try/catch above. That block
// begins with require('dotenv'), which throws on Vercel's production build, so
// anything inside it is silently skipped in prod (this mapping used to live in
// there, which is why LOGO_DEV_API_KEY set in the Vercel dashboard never reached
// the client). This only reads process.env (Vercel dashboard in prod, or dotenv
// locally), exposing LOGO_DEV_API_KEY (pk_…) under the NEXT_PUBLIC_ name
// TickerLogo reads. The sk_ secret is never surfaced to the browser.
if (!process.env.NEXT_PUBLIC_LOGO_DEV_PUBLISHABLE_KEY && process.env.LOGO_DEV_API_KEY) {
  process.env.NEXT_PUBLIC_LOGO_DEV_PUBLISHABLE_KEY = process.env.LOGO_DEV_API_KEY;
}

const SECURITY_HEADERS = [
  { key: 'Strict-Transport-Security', value: 'max-age=31536000' },
  { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), geolocation=(), microphone=()' },
  { key: 'X-DNS-Prefetch-Control', value: 'off' },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: '/:path*',
        headers: SECURITY_HEADERS,
      },
      // Static data JSON shipped from /public — built once by
      // tools/build_frontend_data.py during the daily refresh, then
      // unchanged for ~24h. Aggressive CDN caching means returning
      // visitors don't wake any Vercel function on reload, which is
      // the largest chunk of Active CPU we can shed.
      //
      // s-maxage=300: edge serves fresh for 5 min.
      // stale-while-revalidate=3600: serves stale up to 1h while
      //   revalidating in the background. Combined window covers a
      //   typical user session.
      // max-age=60: browser caches for 1 min (less than CDN so users
      //   notice nightly refresh within a minute of revisiting).
      {
        source: '/screener.json',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600',
          },
        ],
      },
      {
        source: '/weekly.json',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600',
          },
        ],
      },
      {
        source: '/weeks/:file*',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600',
          },
        ],
      },
      {
        source: '/symbols/:file*',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600',
          },
        ],
      },
      // ticker-names.json + ticker-exchanges.json refresh quarterly
      // (SEC EDGAR + GitHub Actions cron) — cache them harder than the
      // daily-data JSON. 1h fresh / 24h stale-while-revalidate is plenty.
      {
        source: '/ticker-names.json',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=3600, s-maxage=3600, stale-while-revalidate=86400',
          },
        ],
      },
      {
        source: '/ticker-exchanges.json',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=3600, s-maxage=3600, stale-while-revalidate=86400',
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
