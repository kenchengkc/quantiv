// US equity market-hours helpers backed by one repository-wide NYSE session
// contract. Full-day holidays and early closes live in config/market_sessions.json;
// frontend, Cloudflare Worker, and Python control paths must consume that same file.

import MARKET_SESSIONS from '../../../config/market_sessions.json';

/** First minute we start hitting Finnhub (slightly before the 09:30 open). */
const QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25; // 09:25 ET
/** Keep a short settle window after the actual regular close. On an early-close
 * session this becomes 13:45 ET rather than incorrectly running until 16:45. */
const QUOTE_SETTLE_MINUTES = 45;

const REGULAR_OPEN_MIN = 9 * 60 + 30; // 09:30 ET
const DEFAULT_REGULAR_CLOSE_MIN = 16 * 60; // 16:00 ET

// Extended-hours windows for "tickers reporting today" focused refresh via
// Alpaca Basic's free IEX feed. On an NYSE early-close session, after-hours starts
// at the actual 13:00 close rather than the normal 16:00 bell.
const PREMARKET_OPEN_MIN = 8 * 60; // 08:00 ET — start of IEX pre-market session
const PREMARKET_CLOSE_MIN = 9 * 60 + 24; // 09:24 ET — hand off to regular refresh
const AFTERHOURS_CLOSE_MIN = 17 * 60; // 17:00 ET — end of IEX post-market session

const NYSE_HOLIDAYS: ReadonlySet<string> = new Set(MARKET_SESSIONS.holidays);
const EARLY_CLOSE_MINUTES: ReadonlyMap<string, number> = new Map(
  Object.entries(MARKET_SESSIONS.early_closes).map(([isoDate, close]) => {
    const [hour, minute] = close.split(':').map(Number);
    return [isoDate, hour * 60 + minute] as const;
  }),
);

interface NowParts {
  weekday: string;
  minutes: number;
  isoDate: string; // YYYY-MM-DD in ET, for session lookup
}

function nowParts(d: Date = new Date()): NowParts {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: MARKET_SESSIONS.timezone,
    weekday: 'short',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const m: Record<string, string> = {};
  for (const p of parts) m[p.type] = p.value;
  return {
    weekday: m.weekday ?? 'Sun',
    // hour can be reported as '24' at midnight ET in some Node builds — clamp.
    minutes: ((parseInt(m.hour ?? '0') % 24) * 60) + parseInt(m.minute ?? '0'),
    isoDate: `${m.year ?? '0000'}-${m.month ?? '01'}-${m.day ?? '01'}`,
  };
}

function isTradingDayET(now: Date): boolean {
  const { weekday, isoDate } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return false;
  return !NYSE_HOLIDAYS.has(isoDate);
}

function regularCloseMinute(isoDate: string): number {
  return EARLY_CLOSE_MINUTES.get(isoDate) ?? DEFAULT_REGULAR_CLOSE_MIN;
}

function quoteRefreshCloseMinute(isoDate: string): number {
  return regularCloseMinute(isoDate) + QUOTE_SETTLE_MINUTES;
}

/** Returns today's date in ET as YYYY-MM-DD. Use this anywhere a route is
 * filtering by "today" — server local time can disagree with ET around UTC
 * midnight. */
export function etDateIso(now: Date = new Date()): string {
  return nowParts(now).isoDate;
}

/** NYSE regular session only — drives "market closed" UI while quotes may still refresh. */
export function isNyseRegularSessionET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes, isoDate } = nowParts(now);
  return minutes >= REGULAR_OPEN_MIN && minutes < regularCloseMinute(isoDate);
}

/** True after the actual regular close on the given ET date. */
export function hasRegularClosePassedET(now: Date = new Date()): boolean {
  const { minutes, isoDate } = nowParts(now);
  return minutes >= regularCloseMinute(isoDate);
}

/** ET date of the most recent trading day whose regular session has fully
 * ended as of `now`. Today counts once its session-specific close has passed;
 * otherwise walk back to the previous trading day. */
export function lastCompletedTradingDayIso(now: Date = new Date()): string {
  if (isTradingDayET(now) && hasRegularClosePassedET(now)) {
    return etDateIso(now);
  }
  const d = new Date(now);
  for (let i = 0; i < 8; i++) {
    d.setUTCDate(d.getUTCDate() - 1);
    if (isTradingDayET(d)) return etDateIso(d);
  }
  return etDateIso(now);
}

/** When cron / batch-price / fast polling should still hit Finnhub. */
export function isQuoteRefreshWindowET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes, isoDate } = nowParts(now);
  return minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= quoteRefreshCloseMinute(isoDate);
}

/** Pre-market window — Alpaca extended-hours quotes for BMO reporters. */
export function isPremarketWindowET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes } = nowParts(now);
  return minutes >= PREMARKET_OPEN_MIN && minutes <= PREMARKET_CLOSE_MIN;
}

/** After-hours window — starts at that session's actual regular close. */
export function isAfterhoursWindowET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes, isoDate } = nowParts(now);
  return minutes >= regularCloseMinute(isoDate) && minutes <= AFTERHOURS_CLOSE_MIN;
}

/** Classify the current minute into the matching window kind, or null. */
export type RefreshWindowKind = 'premarket' | 'regular' | 'afterhours';

export function currentRefreshWindow(now: Date = new Date()): RefreshWindowKind | null {
  if (!isTradingDayET(now)) return null;
  const { minutes, isoDate } = nowParts(now);
  if (minutes >= PREMARKET_OPEN_MIN && minutes <= PREMARKET_CLOSE_MIN) return 'premarket';
  // After-hours takes precedence from the actual session close, including 13:00
  // early closes, so reporting tickers can switch strategies immediately.
  if (minutes >= regularCloseMinute(isoDate) && minutes <= AFTERHOURS_CLOSE_MIN) {
    return 'afterhours';
  }
  if (minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= quoteRefreshCloseMinute(isoDate)) {
    return 'regular';
  }
  return null;
}

/** True while earnings-calendar quote-based moves should read LIVE (vs CLOSE). */
export function areEarningsQuotesLive(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes, isoDate } = nowParts(now);
  if (minutes >= PREMARKET_OPEN_MIN && minutes <= PREMARKET_CLOSE_MIN) return true;
  if (minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= quoteRefreshCloseMinute(isoDate)) return true;
  if (minutes >= regularCloseMinute(isoDate) && minutes < AFTERHOURS_CLOSE_MIN) return true;
  return false;
}

/** Human-readable status string for UI badges. Returns null during regular hours. */
export function marketClosedReason(now: Date = new Date()): string | null {
  const { weekday, minutes, isoDate } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return 'Weekend · last close';
  if (NYSE_HOLIDAYS.has(isoDate)) return 'Market holiday · last close';
  if (minutes < REGULAR_OPEN_MIN) return 'Pre-market · last close';
  const closeMinute = regularCloseMinute(isoDate);
  const settleMinute = quoteRefreshCloseMinute(isoDate);
  if (minutes >= closeMinute && minutes <= settleMinute) return 'After close · quotes settling';
  if (minutes > settleMinute) return 'After-hours · last close';
  return null;
}
