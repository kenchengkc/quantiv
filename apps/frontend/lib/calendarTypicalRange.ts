import { earningsReactionCloseDate } from './earningsReaction';

export type HistoricalReaction = {
  date: string;
  timing?: string;
  actual?: number | null;
  realized_target_protocol?: string | null;
  realized_label_source?: string | null;
};

export type HistoricalTypicalRange = {
  low: number;
  high: number;
  count: number;
  asOf: string;
};

export type CalendarTypicalRanges = Record<string, HistoricalTypicalRange>;

/** Historical context, not a calibrated interval for an IV/straddle forecast. */
export function historicalTypicalRange(
  history: HistoricalReaction[], targetDate: string, asOf: string,
): HistoricalTypicalRange | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate) || !/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return null;
  const seen = new Set<string>();
  const values = history
    .filter((row) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row.date) ||
          row.realized_target_protocol !== 'quantiv.session-reaction.v2' ||
          row.realized_label_source !== 'ohlcv_session_close' ||
          row.actual == null || !Number.isFinite(row.actual)) return false;
      const close = earningsReactionCloseDate(row.date, row.timing);
      return row.date < targetDate && close < targetDate && close <= asOf;
    })
    .sort((a, b) => b.date.localeCompare(a.date))
    .filter((row) => {
      if (seen.has(row.date)) return false;
      seen.add(row.date);
      return true;
    })
    .slice(0, 4)
    .map((row) => Math.abs(row.actual!))
    .sort((a, b) => a - b);
  if (values.length < 2) return null;
  const quantile = (p: number) => {
    const index = (values.length - 1) * p;
    const lo = Math.floor(index);
    return values[lo] + (values[Math.ceil(index)] - values[lo]) * (index - lo);
  };
  return { low: quantile(0.25), high: quantile(0.75), count: values.length, asOf };
}
