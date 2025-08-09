/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    appDir: true,
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
