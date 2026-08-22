import { describe, expect, it } from 'vitest';
import fixture from '../../../tools/fixtures/market-hours.json';
import { currentRefreshWindow, WORKER_NYSE_HOLIDAYS } from './marketHours';

const categories = ['boundary', 'weekend', 'holiday', 'dst', 'precedence'] as const;

describe('currentRefreshWindow', () => {
  for (const category of categories) {
    describe(category, () => {
      it.each(fixture.vectors.filter((vector) => vector.category === category))(
        '$name',
        ({ instant, expected }) => {
          expect(currentRefreshWindow(new Date(instant))).toBe(expected);
        },
      );
    });
  }

  it('matches the shared NYSE holiday set over the common support range', () => {
    const { rangeStart, rangeEnd, dates } = fixture.holidayParity;
    const supportedHolidays = WORKER_NYSE_HOLIDAYS.filter(
      (date) => date >= rangeStart && date <= rangeEnd,
    );

    expect(supportedHolidays).toEqual(dates);
  });
});
