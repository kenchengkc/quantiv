/**
 * Earnings-calendar price reaction: when to show OHLCV realized % vs live quote %.
 *
 * Realized (from build_frontend_data / v_ohlcv, close-to-close):
 *   BMO — prior session close → report-day close
 *   AMC — report-day close → next session close
 *
 * Live (batch-price / IEX) until the realization window ends:
 *   Future reporters — last close → current price
 *   Today BMO — through report-day regular close
 *   Today AMC — through next trading day's regular close (weekends/holidays skipped)
 */
import { MARKET_HOLIDAYS_US } from './marketHolidays.generated';
import { etDateIso, isNyseRegularSessionET } from './marketHours';

export type EarningsTimingBucket = 'bmo' | 'amc' | 'unknown';

export type EarningsReactionTag = 'REALIZED' | 'LIVE';

export type EarningsReactionDisplay = {
  changePct: number | null;
  tag: EarningsReactionTag | null;
};

const HOLIDAYS = new Set<string>(MARKET_HOLIDAYS_US);

export function timingBucket(timing?: string): EarningsTimingBucket {
  const k = (timing ?? '').toLowerCase();
  if (k === 'bmo' || k.includes('before')) return 'bmo';
  if (k === 'amc' || k.includes('after')) return 'amc';
  return 'unknown';
}

function parseIsoDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1));
}

function addCalendarDays(iso: string, days: number): string {
  const d = parseIsoDate(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Next NYSE session on or after the day after `fromIso`. */
export function nextTradingDayIso(fromIso: string): string {
  let cur = addCalendarDays(fromIso, 1);
  for (let i = 0; i < 366; i++) {
    const dow = parseIsoDate(cur).getUTCDay();
    if (dow !== 0 && dow !== 6 && !HOLIDAYS.has(cur)) return cur;
    cur = addCalendarDays(cur, 1);
  }
  return cur;
}

/** Last calendar day that may still show a live quote before switching to realized OHLCV. */
export function earningsReactionCloseDate(
  earningsDate: string,
  timing?: string,
): string {
  return timingBucket(timing) === 'amc'
    ? nextTradingDayIso(earningsDate)
    : earningsDate;
}

/** True once the regular close for the realization window is in (ET). */
export function isRealizationWindowComplete(
  earningsDate: string,
  timing?: string,
  now: Date = new Date(),
): boolean {
  const today = etDateIso(now);
  const closeDate = earningsReactionCloseDate(earningsDate, timing);
  if (today > closeDate) return true;
  if (today < closeDate) return false;
  return !isNyseRegularSessionET(now);
}

/** Whether batch-price polling is still useful for this event. */
export function shouldPollLiveQuote(
  earningsDate: string,
  timing: string | undefined,
  realizedMovePct: number | null | undefined,
  now: Date = new Date(),
): boolean {
  const complete = isRealizationWindowComplete(earningsDate, timing, now);
  if (complete && realizedMovePct != null) return false;
  const today = etDateIso(now);
  return earningsDate > today || !complete;
}

export function resolveEarningsReactionDisplay(args: {
  earningsDate: string;
  timing?: string;
  realizedMovePct?: number | null;
  liveChangePct?: number | null;
  now?: Date;
}): EarningsReactionDisplay {
  const {
    earningsDate,
    timing,
    realizedMovePct = null,
    liveChangePct = null,
    now = new Date(),
  } = args;

  const complete = isRealizationWindowComplete(earningsDate, timing, now);
  const today = etDateIso(now);

  if (complete && realizedMovePct != null) {
    return { changePct: realizedMovePct, tag: 'REALIZED' };
  }

  const showLive = earningsDate > today || !complete;
  if (showLive && liveChangePct != null) {
    return { changePct: liveChangePct, tag: 'LIVE' };
  }

  return { changePct: null, tag: null };
}
