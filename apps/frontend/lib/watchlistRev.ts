import { getRedis } from '@/lib/redis';

// Bumped on every watchlist membership change (add/remove, not reorder).
// The Railway quote worker GETs this key each universe refresh (~5 min) and
// only re-queries the Neon watchlist table when the value changed, so Neon
// can stay suspended all day instead of being woken by polling.
export const WATCHLIST_REV_KEY = 'watchlist:rev';

export async function bumpWatchlistRev(): Promise<void> {
  const redis = getRedis();
  if (!redis) return;
  try {
    await redis.incr(WATCHLIST_REV_KEY);
  } catch {
    // Best-effort: a missed bump only delays re-ranking until the worker's
    // next restart; never block the watchlist mutation on Redis.
  }
}
