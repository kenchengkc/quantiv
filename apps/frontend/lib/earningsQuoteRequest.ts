import { parseMarketSymbol } from './marketSymbol';

export const MAX_QUOTE_BATCH_SYMBOLS = 400;

/** Only validated public ticker identifiers may enter the same-origin quote requests. */
export function earningsQuoteRequestUrls(values: readonly unknown[]): string[] {
  const symbols = new Set<string>();
  for (const value of values) {
    const symbol = parseMarketSymbol(value);
    if (symbol !== null) symbols.add(symbol);
  }

  const allSymbols = [...symbols];
  const urls: string[] = [];
  for (let offset = 0; offset < allSymbols.length; offset += MAX_QUOTE_BATCH_SYMBOLS) {
    const query = new URLSearchParams({
      symbols: allSymbols.slice(offset, offset + MAX_QUOTE_BATCH_SYMBOLS).join(','),
      context: 'earnings',
    });
    urls.push(`/api/stocks/batch-price?${query.toString()}`);
  }
  return urls;
}
