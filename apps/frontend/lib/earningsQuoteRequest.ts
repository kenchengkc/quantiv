import { parseMarketSymbol } from './marketSymbol';

/** Only validated public ticker identifiers may enter the same-origin quote request. */
export function earningsQuoteRequestUrl(values: readonly unknown[]): string | null {
  const symbols = new Set<string>();
  for (const value of values) {
    const symbol = parseMarketSymbol(value);
    if (symbol !== null) symbols.add(symbol);
  }
  if (symbols.size === 0) return null;

  const query = new URLSearchParams({
    symbols: [...symbols].join(','),
    context: 'earnings',
  });
  return `/api/stocks/batch-price?${query.toString()}`;
}
