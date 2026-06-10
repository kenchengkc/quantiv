import { describe, expect, it } from 'vitest';
import { enrichQuoteTick } from './quoteTick';

describe('enrichQuoteTick', () => {
  it('recomputes change from price and previousClose, ignoring stale vendor dp', () => {
    // Finnhub still reports +2.56% dp while pc was overridden to the real prior close.
    const out = enrichQuoteTick({
      symbol: 'JBL',
      price: 370.0,
      previousClose: 373.0,
      change: 9.5,
      changePct: 0.0256,
    });
    expect(out.change).toBeCloseTo(-3.0, 6);
    expect(out.changePct).toBeCloseTo(-3.0 / 373.0, 6);
  });

  it('passes through when price or previousClose is missing', () => {
    expect(
      enrichQuoteTick({
        symbol: 'X',
        price: null,
        previousClose: 100,
        change: 1,
        changePct: 0.01,
      }).changePct,
    ).toBe(0.01);
  });
});
