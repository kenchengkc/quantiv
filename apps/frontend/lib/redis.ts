import { Redis } from '@upstash/redis';

// Upstash Redis HTTP client. Safe to call across Vercel serverless instances —
// unlike the in-memory cache we started with, this is shared state.
// Env: UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN (set in Vercel project).
let client: Redis | null | undefined;

export function getRedis(): Redis | null {
  if (client !== undefined) return client;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    client = null;
    return null;
  }
  client = new Redis({ url, token });
  return client;
}
