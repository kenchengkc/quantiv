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
    ];
  },
};

module.exports = nextConfig;
