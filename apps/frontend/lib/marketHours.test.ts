import { describe, expect, it } from 'vitest';
import { isQuoteRefreshWindowET, marketClosedReason } from './marketHours';

describe('market hours holiday cache', () => {
  it('treats generated US holidays as closed during quote refresh hours', () => {
    const memorialDayMidday = new Date('2026-05-25T16:00:00Z');

    expect(isQuoteRefreshWindowET(memorialDayMidday)).toBe(false);
    expect(marketClosedReason(memorialDayMidday)).toBe('Market holiday · last close');
  });
});
