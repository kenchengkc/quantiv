import { describe, expect, it } from 'vitest';
import {
  calendarCacheKey,
  mergeCalendarReference,
  normalizeCalendarTiming,
  type CalendarReference,
  type ResearchWeek,
} from './calendarReference';

type Event = {
  ticker: string;
  earnings_date: string;
  timing: string;
  em_ml_pct?: number | null;
  em_straddle_pct?: number | null;
  p25?: number | null;
  p75?: number | null;
};

const reference = (events: CalendarReference['events']): CalendarReference => ({
  schema: 'quantiv.calendar-reference.v1',
  release_id: 'calendar-123',
  receipt_id: 'receipt-456',
  observed_at: '2026-09-09T11:00:00Z',
  window: { start: '2026-09-07', end: '2026-10-02' },
  events,
});

const research = (events: Event[]): ResearchWeek<Event> => ({
  metadata: { as_of_date: '2026-09-04', method: 'retained research' },
  window: { start: '2026-09-07', end: '2026-09-11' },
  events,
  summary: { total_events: events.length, avg_em_straddle_pct: 0.08 },
});

describe('calendar reference overlay', () => {
  it('preserves research only for an exact ticker/date/known-session match', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'bmo' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'before_market_open',
        em_ml_pct: 0.06, em_straddle_pct: 0.09, p25: 0.04, p75: 0.08,
      }]),
      '2026-09-07',
    );

    expect(merged.events).toEqual([{
      ticker: 'AAA', earnings_date: '2026-09-08', timing: 'bmo',
      em_ml_pct: 0.06, em_straddle_pct: 0.09, p25: 0.04, p75: 0.08,
    }]);
    expect(merged.metadata.calendar_reference_release_id).toBe('calendar-123');
    expect(merged.metadata.research_as_of_date).toBe('2026-09-04');
  });

  it('renders a revised date without inherited research metrics', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-09', timing: 'bmo' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'bmo', em_ml_pct: 0.06,
      }]),
      '2026-09-07',
    );

    expect(merged.events).toEqual([
      { ticker: 'AAA', earnings_date: '2026-09-09', timing: 'bmo' },
    ]);
  });

  it('renders a changed session without inherited research metrics', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'bmo', em_straddle_pct: 0.09,
      }]),
      '2026-09-07',
    );

    expect(merged.events[0]).toEqual({ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc' });
  });

  it('never inherits research when the reference session is unknown', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown', em_ml_pct: 0.06,
      }]),
      '2026-09-07',
    );

    expect(merged.events[0]).toEqual({ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown' });
  });

  it('uses reference membership: adds dates-only events and removes stale research rows', () => {
    const merged = mergeCalendarReference(
      reference([
        { ticker: 'NEW', earnings_date: '2026-09-10', timing: 'amc' },
        { ticker: 'KEEP', earnings_date: '2026-09-11', timing: 'bmo' },
      ]),
      research([
        { ticker: 'OLD', earnings_date: '2026-09-09', timing: 'amc', em_ml_pct: 0.04 },
        { ticker: 'KEEP', earnings_date: '2026-09-11', timing: 'bmo', em_ml_pct: 0.05 },
      ]),
      '2026-09-07',
    );

    expect(merged.events).toEqual([
      { ticker: 'NEW', earnings_date: '2026-09-10', timing: 'amc' },
      { ticker: 'KEEP', earnings_date: '2026-09-11', timing: 'bmo', em_ml_pct: 0.05 },
    ]);
    expect(merged.summary?.total_events).toBe(2);
  });

  it('does not mutate retained research while overlaying a held release', () => {
    const retained = research([
      { ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc', em_ml_pct: 0.06 },
    ]);
    const before = JSON.stringify(retained);

    mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-09', timing: 'amc' }]),
      retained,
      '2026-09-07',
    );

    expect(JSON.stringify(retained)).toBe(before);
  });

  it('falls back to retained research when no calendar reference is materialized yet', () => {
    const retained = research([
      { ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc', em_ml_pct: 0.06 },
    ]);
    expect(mergeCalendarReference(null, retained, '2026-09-07')).toBe(retained);
  });

  it('keys week caches by calendar release identity', () => {
    expect(calendarCacheKey('release-a', '2026-09-07')).toBe('release-a:2026-09-07');
    expect(calendarCacheKey('release-b', '2026-09-07')).not.toBe(
      calendarCacheKey('release-a', '2026-09-07'),
    );
  });

  it('keeps server and client week anchors on distinct cache keys', () => {
    expect(calendarCacheKey('calendar-123', '2026-09-07')).not.toBe(
      calendarCacheKey('calendar-123', '2026-09-14'),
    );
  });

  it('normalizes provider session spellings conservatively', () => {
    expect(normalizeCalendarTiming('Before market open')).toBe('bmo');
    expect(normalizeCalendarTiming('after-market-close')).toBe('amc');
    expect(normalizeCalendarTiming('During market hours')).toBe('dmh');
    expect(normalizeCalendarTiming('TBD')).toBe('unknown');
  });
});
