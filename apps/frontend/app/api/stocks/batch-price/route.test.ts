import { describe, expect, it } from "vitest";
import {
  cachedQuoteCloseDate,
  isFreshMemoryQuote,
  isUsableSharedQuote,
} from "../../../../lib/quoteCachePolicy";

const cached = {
  at: 0,
  tick: {
    symbol: "AAPL",
    price: 100,
    previousClose: 99,
    change: 1,
    changePct: 1 / 99,
  },
};

describe("batch quote cache policy", () => {
  it("expires process-memory entries after five seconds", () => {
    expect(isFreshMemoryQuote(1_000, 5_999)).toBe(true);
    expect(isFreshMemoryQuote(1_000, 6_000)).toBe(false);
  });

  it("rejects shared entries older than seven days", () => {
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1_000;
    expect(
      isUsableSharedQuote({ ...cached, at: 1_000 }, 1_000 + sevenDaysMs),
    ).toBe(true);
    expect(
      isUsableSharedQuote({ ...cached, at: 1_000 }, 1_001 + sevenDaysMs),
    ).toBe(false);
    expect(isUsableSharedQuote(null, 1_000)).toBe(false);
  });
});


describe("cached closing quote provenance", () => {
  const now = Date.parse('2026-09-24T04:47:00Z');
  const close = {
    ...cached, at: Date.parse('2026-09-23T20:00:06.897Z'),
    source: 'finnhub', session: 'regular', sessionDate: '2026-09-23',
  };

  it('keeps each symbol’s own closing session across ET midnight', () => {
    expect(cachedQuoteCloseDate(close, now)).toBe('2026-09-23');
    expect(cachedQuoteCloseDate({ ...close, at: Date.parse('2026-09-23T19:59:00Z') }, now)).toBeNull();
  });

  it('supports older explicitly regular Finnhub envelopes without sessionDate', () => {
    expect(cachedQuoteCloseDate({ ...close, sessionDate: undefined }, now)).toBe('2026-09-23');
  });

  it.each([
    { source: 'polygon_grouped' },
    { source: 'alpaca_iex', session: 'afterhours' },
    { source: undefined, session: undefined },
    { session: 'premarket' },
    { sessionDate: '2026-09-22' },
    { at: NaN },
    { at: now + 1 },
    { at: Date.parse('2026-09-19T20:30:00Z'), sessionDate: '2026-09-19' },
  ])('does not infer a closing session from ambiguous cache metadata %j', (overrides) => {
    expect(cachedQuoteCloseDate({ ...close, ...overrides }, now)).toBeNull();
  });

  it('uses the actual early close and Eastern date through winter DST', () => {
    const winterNow = Date.parse('2026-11-28T05:47:00Z');
    expect(cachedQuoteCloseDate({ ...close, at: Date.parse('2026-11-27T18:00:01Z'), sessionDate: '2026-11-27' }, winterNow)).toBe('2026-11-27');
    expect(cachedQuoteCloseDate({ ...close, at: Date.parse('2026-11-27T17:59:59Z'), sessionDate: '2026-11-27' }, winterNow)).toBeNull();
  });
});
