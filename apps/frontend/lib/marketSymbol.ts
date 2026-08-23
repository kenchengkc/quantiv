/** A market symbol that is safe to use in cache keys and provider requests. */
export type MarketSymbol = string & { readonly __marketSymbol: unique symbol };

const MARKET_SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;

/**
 * Normalize external, database, or checked-in universe data at its boundary.
 * The result cannot contain separators, query fragments, whitespace, or
 * control characters and is capped at the platform's supported length.
 */
export function parseMarketSymbol(value: unknown): MarketSymbol | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().toUpperCase();
  return MARKET_SYMBOL_RE.test(normalized)
    ? normalized as MarketSymbol
    : null;
}
