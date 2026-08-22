import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getRedis: vi.fn(),
  limiterConstructor: vi.fn(),
  limit: vi.fn(),
  slidingWindow: vi.fn(),
}));

vi.mock('@/lib/redis', () => ({
  getRedis: mocks.getRedis,
}));

vi.mock('@upstash/ratelimit', () => {
  class MockRatelimit {
    static slidingWindow = mocks.slidingWindow;
    limit = mocks.limit;

    constructor(options: unknown) {
      mocks.limiterConstructor(options);
    }
  }

  return { Ratelimit: MockRatelimit };
});

import {
  checkRateLimit,
  clientIdentifier,
  enforceRateLimit,
  PUBLIC_RATE_LIMITS,
  rateLimitEnabled,
  rateLimitHeaders,
  resetRateLimitersForTests,
  tooManyRequests,
} from './rateLimit';

const originalEnabled = process.env.RATE_LIMIT_ENABLED;
const redis = { marker: 'redis' };

function requestWith(headers: HeadersInit = {}): Pick<Request, 'headers'> {
  return { headers: new Headers(headers) };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetRateLimitersForTests();
  delete process.env.RATE_LIMIT_ENABLED;
  mocks.getRedis.mockReturnValue(redis);
  mocks.slidingWindow.mockReturnValue({ algorithm: 'sliding-window' });
  mocks.limit.mockResolvedValue({
    success: true,
    limit: 30,
    remaining: 29,
    reset: 10_000,
  });
});

afterAll(() => {
  if (originalEnabled === undefined) {
    delete process.env.RATE_LIMIT_ENABLED;
  } else {
    process.env.RATE_LIMIT_ENABLED = originalEnabled;
  }
});

describe('clientIdentifier', () => {
  it('prefers the Vercel canonical x-real-ip header', () => {
    expect(
      clientIdentifier(
        requestWith({
          'x-real-ip': '203.0.113.10',
          'x-vercel-forwarded-for': '198.51.100.4',
          'x-forwarded-for': '192.0.2.3',
        }),
      ),
    ).toBe('203.0.113.10');
  });

  it('normalizes forwarded lists, ports, and IPv6 brackets', () => {
    expect(
      clientIdentifier(
        requestWith({
          'x-vercel-forwarded-for': ' unknown, 198.51.100.4:8443, 192.0.2.1',
        }),
      ),
    ).toBe('198.51.100.4');
    expect(
      clientIdentifier(requestWith({ 'x-real-ip': '[2001:DB8::1]:443' })),
    ).toBe('2001:db8::1');
  });

  it('supports the standard Forwarded header and rejects unsafe values', () => {
    expect(
      clientIdentifier(
        requestWith({
          'x-real-ip': 'bad value',
          forwarded: 'for="[2001:db8::2]:4711";proto=https',
        }),
      ),
    ).toBe('2001:db8::2');
  });

  it('uses a stable fallback when proxy headers are absent', () => {
    expect(clientIdentifier(requestWith())).toBe('unknown-client');
  });
});

describe('rate-limit configuration', () => {
  it.each(['0', 'false', 'FALSE', 'off', 'no'])(
    'disables limiting for kill-switch value %s',
    async (value) => {
      process.env.RATE_LIMIT_ENABLED = value;
      expect(rateLimitEnabled()).toBe(false);

      const result = await checkRateLimit(
        requestWith(),
        PUBLIC_RATE_LIMITS.mlPredict,
      );

      expect(result.success).toBe(true);
      expect(mocks.getRedis).not.toHaveBeenCalled();
    },
  );

  it('is enabled by default and fails open without Redis configuration', async () => {
    mocks.getRedis.mockReturnValue(null);

    expect(rateLimitEnabled()).toBe(true);
    await expect(
      checkRateLimit(requestWith(), PUBLIC_RATE_LIMITS.stocksSearch),
    ).resolves.toMatchObject({
      success: true,
      limit: 30,
      remaining: 30,
    });
    expect(mocks.limiterConstructor).not.toHaveBeenCalled();
  });

  it('constructs and reuses one sliding-window limiter per bucket', async () => {
    const request = requestWith({ 'x-real-ip': '203.0.113.7' });

    await checkRateLimit(request, PUBLIC_RATE_LIMITS.mlPredict);
    await checkRateLimit(request, PUBLIC_RATE_LIMITS.mlPredict);
    await checkRateLimit(request, PUBLIC_RATE_LIMITS.mlBatchPredict);

    expect(mocks.slidingWindow).toHaveBeenNthCalledWith(1, 30, '1 m');
    expect(mocks.slidingWindow).toHaveBeenNthCalledWith(2, 10, '1 m');
    expect(mocks.limiterConstructor).toHaveBeenCalledTimes(2);
    expect(mocks.limiterConstructor).toHaveBeenNthCalledWith(1, {
      redis,
      limiter: { algorithm: 'sliding-window' },
      prefix: 'quantiv:ratelimit:ml-predict',
      analytics: false,
    });
    expect(mocks.limit).toHaveBeenCalledTimes(3);
    expect(mocks.limit).toHaveBeenCalledWith('203.0.113.7');
  });
});

describe('rate-limit decisions and responses', () => {
  it('returns the limiter decision without leaking provider-only fields', async () => {
    mocks.limit.mockResolvedValue({
      success: false,
      limit: 10,
      remaining: 0,
      reset: 12_345,
      pending: Promise.resolve(),
    });

    await expect(
      checkRateLimit(
        requestWith({ 'x-real-ip': '192.0.2.10' }),
        PUBLIC_RATE_LIMITS.mlBatchPredict,
      ),
    ).resolves.toEqual({
      success: false,
      limit: 10,
      remaining: 0,
      reset: 12_345,
    });
  });

  it('fails open when Redis rejects the limit operation', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.limit.mockRejectedValue(new Error('redis down'));

    await expect(
      checkRateLimit(requestWith(), PUBLIC_RATE_LIMITS.stocksIntraday),
    ).resolves.toMatchObject({
      success: true,
      limit: 60,
      remaining: 60,
    });
    expect(warning).toHaveBeenCalledOnce();
    warning.mockRestore();
  });

  it('builds standard and compatibility headers for a 429', () => {
    const headers = rateLimitHeaders(
      { success: false, limit: 30, remaining: 0, reset: 12_500 },
      10_000,
    );

    expect(Object.fromEntries(headers.entries())).toMatchObject({
      'cache-control': 'private, no-store, max-age=0, must-revalidate',
      'ratelimit-limit': '30',
      'ratelimit-remaining': '0',
      'ratelimit-reset': '3',
      'retry-after': '3',
      'x-ratelimit-limit': '30',
      'x-ratelimit-remaining': '0',
      'x-ratelimit-reset': '12500',
    });
  });

  it('returns a JSON 429 with retry metadata', async () => {
    const response = tooManyRequests(
      { success: false, limit: 10, remaining: 0, reset: 11_001 },
      10_000,
    );

    expect(response.status).toBe(429);
    expect(response.headers.get('Retry-After')).toBe('2');
    await expect(response.json()).resolves.toEqual({
      error: 'rate_limit_exceeded',
      message: 'Too many requests',
      retryAfter: 2,
    });
  });

  it('enforces denied decisions and passes allowed decisions through', async () => {
    mocks.limit
      .mockResolvedValueOnce({
        success: true,
        limit: 30,
        remaining: 29,
        reset: Date.now() + 60_000,
      })
      .mockResolvedValueOnce({
        success: false,
        limit: 30,
        remaining: 0,
        reset: Date.now() + 60_000,
      });

    await expect(
      enforceRateLimit(requestWith(), PUBLIC_RATE_LIMITS.mlPredict),
    ).resolves.toBeNull();
    const denied = await enforceRateLimit(
      requestWith(),
      PUBLIC_RATE_LIMITS.mlPredict,
    );
    expect(denied?.status).toBe(429);
  });
});
