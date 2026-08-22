import { describe, expect, it } from 'vitest';
import fixture from '../../../tools/fixtures/market-hours.json';
import { MARKET_HOLIDAYS_US } from './marketHolidays.generated';
import { currentRefreshWindow } from './marketHours';

describe('market-hours parity fixture', () => {
  it.each(fixture.vectors)('$name', ({ instant, expected }) => {
    expect(currentRefreshWindow(new Date(instant))).toBe(expected);
  });

  it('matches the shared NYSE holiday set over the common support range', () => {
    const { rangeStart, rangeEnd, dates } = fixture.holidayParity;
    const supportedHolidays = MARKET_HOLIDAYS_US.filter(
      (date) => date >= rangeStart && date <= rangeEnd,
    );

    expect(supportedHolidays).toEqual(dates);
  });
});
