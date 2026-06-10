import { afterEach, describe, expect, it } from 'vitest';
import {
  hasWeekCache,
  readLiveQuoteCache,
  readWeekCache,
  writeLiveQuoteCache,
  writeWeekCache,
} from './earningsCalendarCache';

describe('earningsCalendarCache', () => {
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it('round-trips live quotes through sessionStorage', () => {
    writeLiveQuoteCache({
      JBL: { change: -3, changePct: -0.008, realizedMovePct: null, realizedDate: null },
    });
    expect(readLiveQuoteCache().JBL?.changePct).toBeCloseTo(-0.008, 6);
  });

  it('round-trips week JSON through sessionStorage', () => {
    const week = { window: { start: '2026-06-15' }, events: [] };
    writeWeekCache('2026-06-15', week);
    expect(hasWeekCache('2026-06-15')).toBe(true);
    expect(readWeekCache('2026-06-15')).toEqual(week);
  });
});
