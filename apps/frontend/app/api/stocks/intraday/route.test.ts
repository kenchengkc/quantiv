import type { NextRequest } from 'next/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fetchIntradayBars: vi.fn(),
  getRedis: vi.fn(),
  redisGet: vi.fn(),
  redisSet: vi.fn(),
  enforceRateLimit: vi.fn(),
}));

vi.mock('@/lib/alpaca', () => ({
  fetchIntradayBars: mocks.fetchIntradayBars,
}));

vi.mock('@/lib/redis', () => ({
  getRedis: mocks.getRedis,
}));

vi.mock('@/lib/rateLimit', () => ({
  enforceRateLimit: mocks.enforceRateLimit,
  PUBLIC_RATE_LIMITS: { stocksIntraday: { bucket: 'test' } },
}));

import { GET } from './route';

const goodPayload = {
  bars: [
    { t: '2026-09-02T13:30:00Z', c: 100 },
    { t: '2026-09-02T13:35:00Z', c: 101 },
  ],
  previousClose: 99,
  asOf: '2026-09-02T13:35:00Z',
  sessionDate: '2026-09-02',
  isCurrentSession: false,
};

function request(symbol: string): NextRequest {
  return new Request(
    `http://localhost/api/stocks/intraday?symbol=${encodeURIComponent(symbol)}`,
  ) as unknown as NextRequest;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.enforceRateLimit.mockResolvedValue(null);
  mocks.getRedis.mockReturnValue({
    get: mocks.redisGet,
    set: mocks.redisSet,
  });
  mocks.redisGet.mockResolvedValue(null);
  mocks.redisSet.mockResolvedValue('OK');
});

describe('intraday last-good cache', () => {
  it('persists a confirmed live fetch as both fresh and last-good data', async () => {
    mocks.fetchIntradayBars.mockResolvedValue(goodPayload);

    const response = await GET(request('GOOD1'));
    expect(response.status).toBe(200);
    expect(response.headers.get('x-quantiv-intraday')).toBe('fresh');
    await expect(response.json()).resolves.toEqual(goodPayload);

    expect(mocks.redisSet).toHaveBeenCalledWith(
      'intraday:GOOD1:5Min',
      expect.objectContaining({ payload: goodPayload }),
      { ex: 120 },
    );
    expect(mocks.redisSet).toHaveBeenCalledWith(
      'intraday:last-good:GOOD1:5Min',
      goodPayload,
      { ex: 14 * 24 * 60 * 60 },
    );
  });

  it('serves last-good bars when Alpaca throws', async () => {
    mocks.fetchIntradayBars.mockRejectedValue(new Error('provider down'));
    mocks.redisGet.mockImplementation(async (key: string) =>
      key === 'intraday:last-good:FAIL1:5Min' ? goodPayload : null,
    );

    const response = await GET(request('FAIL1'));
    expect(response.status).toBe(200);
    expect(response.headers.get('x-quantiv-intraday')).toBe('last-good');
    await expect(response.json()).resolves.toEqual(goodPayload);
  });

  it('serves last-good bars when Alpaca returns an empty payload', async () => {
    mocks.fetchIntradayBars.mockResolvedValue({
      bars: [],
      previousClose: null,
      asOf: null,
      sessionDate: null,
      isCurrentSession: false,
    });
    mocks.redisGet.mockImplementation(async (key: string) =>
      key === 'intraday:last-good:EMPTY1:5Min' ? goodPayload : null,
    );

    const response = await GET(request('EMPTY1'));
    expect(response.status).toBe(200);
    expect(response.headers.get('x-quantiv-intraday')).toBe('last-good');
    await expect(response.json()).resolves.toEqual(goodPayload);
    expect(mocks.redisSet).not.toHaveBeenCalled();
  });
});
