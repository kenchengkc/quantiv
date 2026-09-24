import { etDateIso, hasRegularClosePassedET, isTradingDayET } from './marketHours';

export const MEMORY_QUOTE_CACHE_TTL_MS = 5_000;
export const MAX_SHARED_QUOTE_AGE_MS = 7 * 24 * 60 * 60 * 1_000;

export type TimestampedQuote = {
  at: number;
};

export function isFreshMemoryQuote(
  fetchedAt: number,
  nowMs = Date.now(),
): boolean {
  return nowMs - fetchedAt < MEMORY_QUOTE_CACHE_TTL_MS;
}

export function isUsableSharedQuote<T extends TimestampedQuote>(
  raw: T | null | undefined,
  nowMs = Date.now(),
): raw is T {
  return Boolean(
    raw &&
    typeof raw.at === "number" &&
    nowMs - raw.at <= MAX_SHARED_QUOTE_AGE_MS,
  );
}

/** Session of a cached regular-hours quote captured at/after the close.
 * This is a provisional CLOSE, not finalized OHLCV. Never infer a session from
 * a batch timestamp or a grouped-daily cache write (which may occur days later).
 */
export function cachedQuoteCloseDate(
  entry: TimestampedQuote & { source?: string; session?: string; sessionDate?: string },
  nowMs = Date.now(),
): string | null {
  if (
    entry.source !== 'finnhub' || entry.session !== 'regular' ||
    !Number.isFinite(entry.at) || entry.at > nowMs
  ) return null;
  const captured = new Date(entry.at);
  if (!Number.isFinite(captured.getTime())) return null;
  const date = etDateIso(captured);
  if (entry.sessionDate != null && entry.sessionDate !== date) return null;
  return isTradingDayET(captured) && hasRegularClosePassedET(captured) ? date : null;
}
