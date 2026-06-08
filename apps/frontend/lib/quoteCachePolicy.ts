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
