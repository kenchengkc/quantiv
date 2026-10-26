import { describe, expect, it } from 'vitest';
import { earningsQuoteRequestUrls } from './earningsQuoteRequest';

describe('earnings quote request boundary', () => {
  it.each([
    [400, [400]],
    [401, [400, 1]],
    [800, [400, 400]],
    [801, [400, 400, 1]],
  ])('keeps all %i tickers within the server limit without an empty final request', (count, sizes) => {
    const symbols = Array.from({ length: count }, (_, index) =>
      `Q${String.fromCharCode(65 + Math.floor(index / (26 * 26)))}${String.fromCharCode(65 + Math.floor(index / 26) % 26)}${String.fromCharCode(65 + index % 26)}`,
    );
    const paths = earningsQuoteRequestUrls([...symbols, symbols[0].toLowerCase(), null]);
    const batches = paths.map((path) =>
      new URL(path, 'https://quantiv.example').searchParams.get('symbols')!.split(','),
    );
    expect(batches.map((batch) => batch.length)).toEqual(sizes);
    expect(batches.flat()).toEqual(symbols);
  });

  it('normalizes and deduplicates public tickers on the fixed same-origin endpoint', () => {
    const paths = earningsQuoteRequestUrls([' aapl ', 'AAPL', 'BRK-B', 'BF.B']);
    expect(paths).toHaveLength(1);
    const url = new URL(paths[0], 'https://quantiv.example');
    expect(url.origin).toBe('https://quantiv.example');
    expect(url.pathname).toBe('/api/stocks/batch-price');
    expect([...url.searchParams.entries()]).toEqual([
      ['symbols', 'AAPL,BRK-B,BF.B'],
      ['context', 'earnings'],
    ]);
  });

  it('drops file contents and query injection attempts instead of transmitting them', () => {
    const path = earningsQuoteRequestUrls([
      'MSFT', 'AAPL&context=watchlist', 'AAPL,TSLA', '../secret',
      '//example.com', 'token=private', 'AAPL\nprivate', 'TOO-LONG-SYMBOL',
      null, undefined, 42, { ticker: 'NVDA' },
    ]);
    expect(path).toEqual(['/api/stocks/batch-price?symbols=MSFT&context=earnings']);
  });

  it('skips the request when no valid symbols remain', () => {
    expect(earningsQuoteRequestUrls([])).toEqual([]);
    expect(earningsQuoteRequestUrls(['AAPL?token=secret', null])).toEqual([]);
  });
});
