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
    dotenv.config({ path: envPath });
  }
} catch (_) {
  // dotenv not available in production build — env vars come from Vercel
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: '/api/:path*',
        headers: [
          {
            key: 'Cache-Control',
            value: 's-maxage=60, stale-while-revalidate=120',
          },
        ],
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
      // ticker-names.json refreshes quarterly (SEC EDGAR + GitHub
      // Actions cron) — cache it harder than the daily-data JSON.
      // 1h fresh / 24h stale-while-revalidate is plenty.
      {
        source: '/ticker-names.json',
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
