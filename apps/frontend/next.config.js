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
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || '',
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL || '',
  },
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
    ];
  },
  async rewrites() {
    return [
      {
        source: '/:symbol',
        destination: '/:symbol',
        has: [
          {
            type: 'query',
            key: 'symbol',
            value: '(?<symbol>.*)',
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
