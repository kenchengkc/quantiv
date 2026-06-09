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
 *
 * Close — same quote as LIVE but shown when the market is closed or quotes
 *   are no longer refreshing (weekends/holidays, after 17:00 ET IEX cutoff).
 */
import { MARKET_HOLIDAYS_US } from './marketHolidays.generated';
import { areEarningsQuotesLive, etDateIso, hasRegularClosePassedET } from './marketHours';

export type EarningsTimingBucket = 'bmo' | 'amc' | 'unknown';

export type EarningsReactionTag = 'REALIZED' | 'LIVE' | 'CLOSE';

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
  return hasRegularClosePassedET(now);
}

/** The realization window has closed but OHLCV hasn't been recorded yet (the
 *  nightly build runs after the close). On the realization day itself, the live
 *  quote's close-to-close move equals what the realized move will be — for a
 *  BMO reporter, today's quote anchors prior-close → report-day close; for an
 *  AMC reporter, the next session's quote anchors report-day close → next close.
 *  So we keep showing LIVE as a faithful stand-in until realized lands. Bounded
 *  to the realization day: after it, previousClose rolls forward and the live
 *  move would no longer match the earnings reaction. */
export function isRealizedPending(
  earningsDate: string,
  timing: string | undefined,
  realizedMovePct: number | null | undefined,
  now: Date = new Date(),
): boolean {
  if (realizedMovePct != null) return false;
  if (!isRealizationWindowComplete(earningsDate, timing, now)) return false;
  return etDateIso(now) === earningsReactionCloseDate(earningsDate, timing);
}

/** The realization window has closed but the nightly-built realized move still
 *  hasn't landed. Fetch once so the post-close KV backfill (realized:{SYM},
 *  written by /api/cron/refresh-broad) can fill the calendar in the ≈15 h gap
 *  before the morning build. Bounded a few days back so an event that never
 *  receives a realized value doesn't poll forever. */
export function shouldFetchRealizedBackfill(
  earningsDate: string,
  timing: string | undefined,
  realizedMovePct: number | null | undefined,
  now: Date = new Date(),
): boolean {
  if (realizedMovePct != null) return false;
  if (!isRealizationWindowComplete(earningsDate, timing, now)) return false;
  const cutoff = etDateIso(new Date(now.getTime() - 6 * 24 * 60 * 60 * 1000));
  return earningsDate >= cutoff;
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
  return (
    earningsDate > today ||
    !complete ||
    isRealizedPending(earningsDate, timing, realizedMovePct, now)
  );
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

  const showQuote =
    earningsDate > today ||
    !complete ||
    isRealizedPending(earningsDate, timing, realizedMovePct, now);
  if (showQuote && liveChangePct != null) {
    const tag: EarningsReactionTag = areEarningsQuotesLive(now) ? 'LIVE' : 'CLOSE';
    return { changePct: liveChangePct, tag };
  }

  return { changePct: null, tag: null };
}
