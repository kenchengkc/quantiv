import { describe, expect, it } from 'vitest';
import {
  areEarningsQuotesLive,
  isQuoteRefreshWindowET,
  marketClosedReason,
} from './marketHours';

describe('market hours holiday cache', () => {
  it('treats generated US holidays as closed during quote refresh hours', () => {
    const memorialDayMidday = new Date('2026-05-25T16:00:00Z');

    expect(isQuoteRefreshWindowET(memorialDayMidday)).toBe(false);
    expect(marketClosedReason(memorialDayMidday)).toBe('Market holiday · last close');
    expect(areEarningsQuotesLive(memorialDayMidday)).toBe(false);
  });

  it('areEarningsQuotesLive is false on weekends and after IEX hours', () => {
    expect(areEarningsQuotesLive(new Date('2026-05-30T15:00:00Z'))).toBe(false); // Sat
    expect(areEarningsQuotesLive(new Date('2026-06-01T23:27:00Z'))).toBe(false); // 19:27 ET
    expect(areEarningsQuotesLive(new Date('2026-06-01T21:00:00Z'))).toBe(false); // 17:00 ET
  });

  it('areEarningsQuotesLive is true during RTH and IEX extended windows', () => {
    expect(areEarningsQuotesLive(new Date('2026-05-28T19:59:00Z'))).toBe(true); // 15:59 ET
    expect(areEarningsQuotesLive(new Date('2026-05-22T20:30:00Z'))).toBe(true); // 16:30 ET Fri
    expect(areEarningsQuotesLive(new Date('2026-05-28T12:00:00Z'))).toBe(true); // 08:00 ET pre-market
  });
});
