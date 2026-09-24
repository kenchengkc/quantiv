import { describe, expect, it } from 'vitest';
import {
  calendarCacheKey,
  daysUntilEarnings,
  displayEarningsDate,
  mergeCalendarReference,
  normalizeCalendarTiming,
  publishedEventForTicker,
  researchMatchesPublishedDate,
  type CalendarReference,
  type ResearchWeek,
} from './calendarReference';

type Event = {
  ticker: string;
  earnings_date: string;
  timing: string;
  em_ml_pct?: number | null;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  hist_move_med_4q?: number | null;
  hist_move_avg_4q?: number | null;
  display_forecast_pct?: number | null;
  display_forecast_method?: 'ml' | 'options_math' | 'options_indicative' | 'historical' | 'historical_prior' | null;
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
  it('preserves research for an exact ticker/date/known-session match', () => {
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

  it('hydrates a matched legacy history-only row so the calendar does not render a dash', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'APOG', earnings_date: '2026-09-22', timing: 'bmo' }]),
      research([{
        ticker: 'APOG',
        earnings_date: '2026-09-22',
        timing: 'before_market_open',
        em_ml_pct: null,
        em_straddle_pct: null,
        em_iv_pct: null,
        hist_move_avg_4q: 0.102694,
      }]),
      '2026-09-21',
    );

    expect(merged.events[0]).toMatchObject({
      ticker: 'APOG',
      earnings_date: '2026-09-22',
      timing: 'bmo',
      display_forecast_pct: 0.102694,
      display_forecast_method: 'historical',
    });
  });

  it('preserves matched ML research when the reference session is unknown', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'FDX', earnings_date: '2026-09-16', timing: 'unknown' }]),
      research([{
        ticker: 'FDX', earnings_date: '2026-09-16', timing: 'after_market_close',
        em_ml_pct: 0.039443, em_straddle_pct: 0.040161, p25: 0.019459, p75: 0.063145,
      }]),
      '2026-09-14',
    );

    expect(merged.events[0]).toEqual({
      ticker: 'FDX', earnings_date: '2026-09-16', timing: 'amc',
      em_ml_pct: 0.039443, em_straddle_pct: 0.040161, p25: 0.019459, p75: 0.063145,
    });
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

  it('renders a changed known session without inherited research metrics', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'bmo', em_straddle_pct: 0.09,
      }]),
      '2026-09-07',
    );

    expect(merged.events[0]).toEqual({ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc' });
  });

  it('renders a known reference session without inheriting unknown-session research', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown', em_ml_pct: 0.06,
      }]),
      '2026-09-07',
    );

    expect(merged.events[0]).toEqual({ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'amc' });
  });

  it('overlays research when ticker/date match and both sessions are unknown', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown' }]),
      research([{
        ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown',
        em_ml_pct: 0.06, em_straddle_pct: 0.09,
      }]),
      '2026-09-07',
    );

    expect(merged.events[0]).toEqual({
      ticker: 'AAA', earnings_date: '2026-09-08', timing: 'unknown',
      em_ml_pct: 0.06, em_straddle_pct: 0.09,
    });
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

  it('falls back to retained research when the reference does not cover the selected week', () => {
    const retained = research([
      { ticker: 'CCL', earnings_date: '2026-09-28', timing: 'bmo', em_ml_pct: 0.05 },
      { ticker: 'NKE', earnings_date: '2026-09-28', timing: 'amc', em_ml_pct: 0.04 },
    ]);
    retained.window = { start: '2026-09-28', end: '2026-10-02' };
    const staleReference = {
      ...reference([]),
      window: { start: '2026-08-31', end: '2026-09-25' },
    };

    expect(mergeCalendarReference(staleReference, retained, '2026-09-28')).toBe(retained);
  });

  it('falls back when the reference covers only part of the selected week', () => {
    const retained = research([
      { ticker: 'MU', earnings_date: '2026-09-30', timing: 'amc', em_ml_pct: 0.07 },
    ]);
    retained.window = { start: '2026-09-28', end: '2026-10-02' };
    const partialReference = {
      ...reference([{ ticker: 'MU', earnings_date: '2026-09-30', timing: 'amc' }]),
      window: { start: '2026-09-07', end: '2026-09-30' },
    };

    expect(mergeCalendarReference(partialReference, retained, '2026-09-28')).toBe(retained);
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

  it('uses the upcoming calendar date instead of a stale research print', () => {
    const published = publishedEventForTicker(
      [
        { ticker: 'TEST', earnings_date: '2026-09-17', timing: 'unknown' },
        { ticker: 'PRGS', earnings_date: '2026-09-29', timing: 'unknown' },
      ],
      'TEST',
      '2026-09-15',
    );
    expect(published?.earnings_date).toBe('2026-09-17');
    expect(displayEarningsDate({
      todayIso: '2026-09-15',
      publishedDate: published?.earnings_date,
      nextEarnings: '2026-08-27',
      researchDate: '2026-08-27',
    })).toBe('2026-09-17');
    expect(daysUntilEarnings('2026-09-17', '2026-09-15')).toBe(2);
    expect(daysUntilEarnings('2026-09-15', '2026-09-15')).toBe(0);
    expect(daysUntilEarnings('2026-08-27', '2026-09-15')).toBeNull();
    expect(researchMatchesPublishedDate('2026-08-27', '2026-09-17')).toBe(false);
  });

  it('prefers an upcoming research date when the calendar has not published yet', () => {
    expect(displayEarningsDate({
      todayIso: '2026-09-15',
      nextEarnings: '2026-08-27',
      researchDate: '2026-09-29',
    })).toBe('2026-09-29');
  });

  it('prefers the soonest upcoming published print when a ticker has two dates', () => {
    expect(
      publishedEventForTicker(
        [
          { ticker: 'CNXC', earnings_date: '2026-09-09', timing: 'amc' },
          { ticker: 'CNXC', earnings_date: '2026-09-29', timing: 'unknown' },
        ],
        'cnxc',
        '2026-09-15',
      )?.earnings_date,
    ).toBe('2026-09-29');
  });
});

it('applies issuer corrections to retained references without reusing the superseded forecast', () => {
  const stale = { ticker: 'HUBG', earnings_date: '2026-09-17', timing: 'unknown' };
  const merged = mergeCalendarReference(reference([stale]), research([{ ...stale, em_ml_pct: .17 }]), '2026-09-14');
  expect(merged.events).toHaveLength(1);
  expect(merged.events[0].earnings_date).toBe('2026-09-14');
  expect(merged.events[0].timing).toBe('bmo');
  expect((merged.events[0] as Event).em_ml_pct).toBeUndefined();
  expect(publishedEventForTicker([stale], 'HUBG', '2026-09-24')?.earnings_date).toBe('2026-09-14');
  expect(publishedEventForTicker(merged.events, 'HUBG', '2026-09-24')?.earnings_date).toBe('2026-09-14');
});
