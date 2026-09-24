import { describe, expect, it } from 'vitest';
import {
  mergeCalendarReference,
  type CalendarReference,
  type ResearchWeek,
} from './calendarReference';

type LegacyEvent = {
  ticker: string;
  earnings_date: string;
  timing: string;
  em_ml_pct?: number | null;
  em_straddle_pct?: number | null;
  hist_move_med_4q?: number | null;
  hist_move_avg_4q?: number | null;
};

function reference(events: CalendarReference['events']): CalendarReference {
  return {
    schema: 'quantiv.calendar-reference.v1',
    release_id: 'calendar-current',
    window: { start: '2026-09-21', end: '2026-09-25' },
    events,
  };
}

function research(events: LegacyEvent[]): ResearchWeek<LegacyEvent> {
  return {
    metadata: { as_of_date: '2026-09-14' },
    window: { start: '2026-09-21', end: '2026-09-25' },
    events,
  };
}

describe('calendar legacy display forecast compatibility', () => {
  it('uses only same-ticker history after an earnings-date revision, never stale ML/options', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'TEST', earnings_date: '2026-09-22', timing: 'unknown' }]),
      research([
        {
          ticker: 'TEST',
          earnings_date: '2026-09-21',
          timing: 'after_market_close',
          em_ml_pct: 0.0767,
          em_straddle_pct: 0.1312,
          hist_move_avg_4q: 0.056849,
        },
      ]),
      '2026-09-21',
    );

    expect(merged.events[0]).toEqual({
      ticker: 'TEST',
      earnings_date: '2026-09-22',
      timing: 'unknown',
      display_forecast_pct: 0.056849,
      display_forecast_method: 'historical',
    });
  });

  it('uses a legacy historical prior for a calendar event absent from the pinned research week', () => {
    const merged = mergeCalendarReference(
      reference([{ ticker: 'GIS', earnings_date: '2026-09-23', timing: 'bmo' }]),
      research([
        {
          ticker: 'AAA',
          earnings_date: '2026-09-22',
          timing: 'bmo',
          hist_move_med_4q: 0.04,
        },
        {
          ticker: 'BBB',
          earnings_date: '2026-09-24',
          timing: 'amc',
          hist_move_avg_4q: 0.08,
        },
      ]),
      '2026-09-21',
    );

    expect(merged.events[0]).toEqual({
      ticker: 'GIS',
      earnings_date: '2026-09-23',
      timing: 'bmo',
      display_forecast_pct: 0.06,
      display_forecast_method: 'historical_prior',
    });
  });
});
