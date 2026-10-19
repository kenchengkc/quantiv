import { historicalTypicalRange, type CalendarTypicalRanges, type HistoricalReaction } from './calendarTypicalRange';
import { readPublicJson } from './researchSnapshot.server';
import type { CalendarReferenceEvent } from './calendarReference';
import type { WeeklyData } from '@/components/EarningsGrid';

/** Build the small calendar range index from the same materialized publication. */
export function readCalendarTypicalRanges(events: CalendarReferenceEvent[], initialWeek: WeeklyData): CalendarTypicalRanges {
  const weeks = new Map<string, WeeklyData | null>([[initialWeek.window.start, initialWeek]]);
  const allEvents = [...events];
  // Navigation can extend beyond the independently published calendar reference.
  // Include research rows from every visible week, just as the grid does there.
  for (let offset = -1; offset <= 3; offset += 1) {
    const start = new Date(`${initialWeek.window.start}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() + offset * 7);
    const key = start.toISOString().slice(0, 10);
    if (!weeks.has(key)) weeks.set(key, readPublicJson<WeeklyData>('weeks', `${key}.json`));
    allEvents.push(...(weeks.get(key)?.events ?? []));
  }
  const histories = new Map<string, HistoricalReaction[]>();
  const ranges: CalendarTypicalRanges = {};
  const uniqueEvents = new Map(allEvents.map((event) => [`${event.ticker}|${event.earnings_date}`, event]));
  for (const event of uniqueEvents.values()) {
    if (!/^[A-Z0-9.^-]+$/.test(event.ticker)) continue;
    const monday = new Date(`${event.earnings_date}T00:00:00Z`);
    if (!Number.isFinite(monday.getTime())) continue;
    monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7);
    const weekKey = monday.toISOString().slice(0, 10);
    if (!weeks.has(weekKey)) weeks.set(weekKey, readPublicJson<WeeklyData>('weeks', `${weekKey}.json`));
    const week = weeks.get(weekKey);
    const research = week?.events.find((row) => row.ticker === event.ticker && row.earnings_date === event.earnings_date);
    const asOf = research?.display_forecast_as_of ?? week?.metadata.as_of_date;
    if (!asOf) continue;
    if (!histories.has(event.ticker)) {
      const detail = readPublicJson<{ earnings_history?: HistoricalReaction[] }>('symbols', `${event.ticker}.json`);
      histories.set(event.ticker, detail?.earnings_history ?? []);
    }
    const range = historicalTypicalRange(histories.get(event.ticker)!, event.earnings_date, asOf);
    if (range) ranges[`${event.ticker}|${event.earnings_date}`] = range;
  }
  return ranges;
}
