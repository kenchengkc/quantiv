const path = require('path');
const dotenv = require('dotenv');

// Load env from repo-level config/.env.*
const repoRoot = path.resolve(__dirname, '..', '..');
const envFile = (process.env.NODE_ENV === 'production' || process.env.ENVIRONMENT === 'production')
  ? '.env.production'
  : '.env.local';
dotenv.config({ path: path.join(repoRoot, 'config', envFile) });

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
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
