import { describe, expect, it } from 'vitest';
import { historicalTypicalRange } from './calendarTypicalRange';

const row = (date: string, actual: number, timing = 'bmo') => ({
  date, actual, timing, realized_target_protocol: 'quantiv.session-reaction.v2',
  realized_label_source: 'ohlcv_session_close',
});

describe('calendar historical typical range', () => {
  it('uses the middle 50% of the latest four verified absolute reactions', () => {
    expect(historicalTypicalRange([
      row('2025-01-10', 0.8), row('2025-04-10', -0.02),
      row('2025-07-10', 0.04), row('2025-10-10', -0.06), row('2026-01-09', 0.08),
    ], '2026-04-10', '2026-04-09')).toEqual({
      low: 0.035, high: 0.065, count: 4, asOf: '2026-04-09',
    });
  });

  it('excludes the target, later outcomes, and reactions incomplete at the forecast cutoff', () => {
    expect(historicalTypicalRange([
      row('2025-07-10', 0), row('2025-10-10', 0.04),
      row('2026-01-09', 0.8, 'amc'), // Friday reaction completes Monday, after the cutoff.
      row('2026-01-12', 0.9), row('2026-04-10', 0.9),
    ], '2026-01-12', '2026-01-09')).toEqual({
      low: 0.01, high: 0.03, count: 2, asOf: '2026-01-09',
    });
  });

  it('requires two distinct verified events and rejects invalid or unverified observations', () => {
    expect(historicalTypicalRange([
      row('2025-10-10', 0.04), row('2025-10-10', 0.04),
      row('2025-07-10', Number.NaN), { date: '2025-04-10', actual: 0.8 },
    ], '2026-01-12', '2026-01-09')).toBeNull();
  });

  it('never substitutes an event-day close for information available before that event', () => {
    const range = historicalTypicalRange([
      row('2025-07-10', 0.02), row('2025-10-10', 0.06),
      row('2026-01-09', 0.8, 'amc'),
    ], '2026-01-12', '2026-01-16');
    expect(range).toMatchObject({ count: 2, asOf: '2026-01-16' });
    expect(range?.low).toBeCloseTo(0.03);
    expect(range?.high).toBeCloseTo(0.05);
  });
});
