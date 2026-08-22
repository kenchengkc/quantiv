import { Ratelimit } from '@upstash/ratelimit';
import { getRedis } from './redis';

export type RateLimitPolicy = Readonly<{
  bucket: string;
  limit: number;
  window: Parameters<typeof Ratelimit.slidingWindow>[1];
}>;

export type RateLimitDecision = {
  success: boolean;
  limit: number;
  remaining: number;
  reset: number;
};

export const PUBLIC_RATE_LIMITS = {
  stocksBatchPrice: {
    bucket: 'stocks-batch-price',
    limit: 120,
    window: '1 m',
  },
  stocksIntraday: {
    bucket: 'stocks-intraday',
    limit: 60,
    window: '1 m',
  },
  stocksSearch: {
    bucket: 'stocks-search',
    limit: 30,
    window: '1 m',
  },
  mlPredict: {
    bucket: 'ml-predict',
    limit: 30,
    window: '1 m',
  },
  mlBatchPredict: {
    bucket: 'ml-batch-predict',
    limit: 10,
    window: '1 m',
  },
} as const satisfies Record<string, RateLimitPolicy>;

const limiters = new Map<string, Ratelimit>();

function normalizedHeaderValue(value: string | null): string | null {
  if (!value) return null;

  for (const part of value.split(',')) {
    let candidate = part.trim();
    if (!candidate) continue;

    if (candidate.toLowerCase().startsWith('for=')) {
      candidate = candidate.slice(4).split(';', 1)[0].trim();
    }
    candidate = candidate.replace(/^"(.*)"$/, '$1');

    const bracketedIpv6 = candidate.match(/^\[([^\]]+)\](?::\d+)?$/);
    if (bracketedIpv6) {
      candidate = bracketedIpv6[1];
    } else if (/^[^:]+:\d+$/.test(candidate)) {
      // Strip a port from IPv4 addresses and hostnames, but leave bare IPv6
      // addresses (which contain multiple colons) intact.
      candidate = candidate.replace(/:\d+$/, '');
    }

    candidate = candidate.trim().toLowerCase();
    if (
      candidate &&
      candidate !== 'unknown' &&
      candidate.length <= 128 &&
      !/[\s\x00-\x1f\x7f]/.test(candidate)
    ) {
      return candidate;
    }
  }
  return null;
}

/**
 * Vercel's canonical client-IP header is x-real-ip. The forwarded headers are
 * retained as fallbacks for rewrites, local reverse proxies, and non-Vercel
 * deployments. A stable shared fallback is safer than a per-request value,
 * which would let requests without proxy headers bypass the limiter.
 */
export function clientIdentifier(request: Pick<Request, 'headers'>): string {
  const candidates = [
    request.headers.get('x-real-ip'),
    request.headers.get('x-vercel-forwarded-for'),
    request.headers.get('x-forwarded-for'),
    request.headers.get('cf-connecting-ip'),
    request.headers.get('forwarded'),
  ];
  for (const candidate of candidates) {
    const normalized = normalizedHeaderValue(candidate);
    if (normalized) return normalized;
  }
  return 'unknown-client';
}

export function rateLimitEnabled(): boolean {
  const value = process.env.RATE_LIMIT_ENABLED?.trim().toLowerCase();
  return value !== '0' && value !== 'false' && value !== 'off' && value !== 'no';
}

function getLimiter(policy: RateLimitPolicy): Ratelimit | null {
  const cached = limiters.get(policy.bucket);
  if (cached) return cached;

  const redis = getRedis();
  if (!redis) return null;

  const limiter = new Ratelimit({
    redis,
    limiter: Ratelimit.slidingWindow(policy.limit, policy.window),
    prefix: `quantiv:ratelimit:${policy.bucket}`,
    analytics: false,
  });
  limiters.set(policy.bucket, limiter);
  return limiter;
}

function failOpenDecision(policy: RateLimitPolicy): RateLimitDecision {
  return {
    success: true,
    limit: policy.limit,
    remaining: policy.limit,
    reset: Date.now(),
  };
}

export async function checkRateLimit(
  request: Pick<Request, 'headers'>,
  policy: RateLimitPolicy,
): Promise<RateLimitDecision> {
  if (!rateLimitEnabled()) return failOpenDecision(policy);

  const limiter = getLimiter(policy);
  if (!limiter) return failOpenDecision(policy);

  try {
    const result = await limiter.limit(clientIdentifier(request));
    return {
      success: result.success,
      limit: result.limit,
      remaining: result.remaining,
      reset: result.reset,
    };
  } catch (error) {
    // Availability takes precedence over abuse protection during a Redis
    // outage. Do not include identifiers or credentials in the log.
    console.warn('[rate-limit] Redis unavailable; allowing request', error);
    return failOpenDecision(policy);
  }
}

function retryAfterSeconds(decision: RateLimitDecision, now = Date.now()): number {
  return Math.max(1, Math.ceil((decision.reset - now) / 1_000));
}

export function rateLimitHeaders(
  decision: RateLimitDecision,
  now = Date.now(),
): Headers {
  const retryAfter = retryAfterSeconds(decision, now);
  return new Headers({
    'Cache-Control': 'private, no-store, max-age=0, must-revalidate',
    'Retry-After': String(retryAfter),
    'RateLimit-Limit': String(decision.limit),
    'RateLimit-Remaining': String(decision.remaining),
    'RateLimit-Reset': String(retryAfter),
    'X-RateLimit-Limit': String(decision.limit),
    'X-RateLimit-Remaining': String(decision.remaining),
    'X-RateLimit-Reset': String(decision.reset),
  });
}

export function tooManyRequests(
  decision: RateLimitDecision,
  now = Date.now(),
): Response {
  const retryAfter = retryAfterSeconds(decision, now);
  return Response.json(
    {
      error: 'rate_limit_exceeded',
      message: 'Too many requests',
      retryAfter,
    },
    {
      status: 429,
      headers: rateLimitHeaders(decision, now),
    },
  );
}

export async function enforceRateLimit(
  request: Pick<Request, 'headers'>,
  policy: RateLimitPolicy,
): Promise<Response | null> {
  const decision = await checkRateLimit(request, policy);
  return decision.success ? null : tooManyRequests(decision);
}

export function resetRateLimitersForTests(): void {
  limiters.clear();
}
