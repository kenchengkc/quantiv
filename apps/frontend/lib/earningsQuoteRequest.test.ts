import { describe, expect, it } from 'vitest';
import { earningsQuoteRequestUrl } from './earningsQuoteRequest';

describe('earnings quote request boundary', () => {
  it('normalizes and deduplicates public tickers on the fixed same-origin endpoint', () => {
    const path = earningsQuoteRequestUrl([' aapl ', 'AAPL', 'BRK-B', 'BF.B']);
    const url = new URL(path!, 'https://quantiv.example');
    expect(url.origin).toBe('https://quantiv.example');
    expect(url.pathname).toBe('/api/stocks/batch-price');
    expect([...url.searchParams.entries()]).toEqual([
      ['symbols', 'AAPL,BRK-B,BF.B'],
      ['context', 'earnings'],
    ]);
  });

  it('drops file contents and query injection attempts instead of transmitting them', () => {
    const path = earningsQuoteRequestUrl([
      'MSFT', 'AAPL&context=watchlist', 'AAPL,TSLA', '../secret',
      '//example.com', 'token=private', 'AAPL\nprivate', 'TOO-LONG-SYMBOL',
      null, undefined, 42, { ticker: 'NVDA' },
    ]);
    expect(path).toBe('/api/stocks/batch-price?symbols=MSFT&context=earnings');
  });

  it('skips the request when no valid symbols remain', () => {
    expect(earningsQuoteRequestUrl([])).toBeNull();
    expect(earningsQuoteRequestUrl(['AAPL?token=secret', null])).toBeNull();
  });
});
