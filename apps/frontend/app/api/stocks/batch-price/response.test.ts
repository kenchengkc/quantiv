import type { NextRequest } from 'next/server';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ mget: vi.fn() }));
vi.mock('@/lib/redis', () => ({ getRedis: () => ({ mget: mocks.mget }) }));
vi.mock('@/lib/rateLimit', () => ({
  enforceRateLimit: async () => null,
  PUBLIC_RATE_LIMITS: { stocksBatchPrice: {} },
}));
vi.mock('@/lib/quoteInterest', () => ({
  interestContext: () => 'earnings', writeQuoteInterest: async () => undefined,
}));
import { GET } from './route';

afterEach(() => vi.useRealTimers());

it('serves each closing session from its own cached quote, alongside the realized overlay', async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-24T04:47:00Z'));
  const quote = (symbol: string, timestamp: string) => ({
    at: Date.parse(timestamp), source: 'finnhub', session: 'regular',
    tick: { symbol, price: 192.03, previousClose: 198.8, change: null, changePct: null },
  });
  const values: Record<string, unknown> = {
    'quote:CTAS': quote('CTAS', '2026-09-23T20:00:06Z'),
    // A fresher quote elsewhere in the batch must not date this older quote.
    'quote:PAYX': quote('PAYX', '2026-09-22T20:05:00Z'),
    'quote:GIS': quote('GIS', '2026-09-23T13:25:00Z'),
    'quote:KBH': quote('KBH', '2026-09-23T19:59:13.115Z'),
    'realized:PAYX': { date: '2026-09-23', pct: -0.0847 },
  };
  mocks.mget.mockImplementation(async (...keys: string[]) => keys.map((key) => values[key] ?? null));
  const response = await GET(new Request('http://localhost/api/stocks/batch-price?symbols=CTAS,PAYX,GIS,KBH&context=earnings') as NextRequest);
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.updated).toBe('2026-09-23T20:00:06.000Z');
  expect(body.data[0]).toMatchObject({ symbol: 'CTAS', quoteCloseDate: '2026-09-23', realizedMovePct: null });
  expect(body.data[0].changePct).toBeCloseTo(-0.0340543);
  expect(body.data[1]).toMatchObject({ symbol: 'PAYX', quoteCloseDate: '2026-09-22', realizedDate: '2026-09-23', realizedMovePct: -0.0847 });
  expect(body.data[2]).toMatchObject({ symbol: 'GIS', quoteCloseDate: null });
  expect(body.data[3]).toMatchObject({ symbol: 'KBH', quoteCloseDate: '2026-09-23' });
});
