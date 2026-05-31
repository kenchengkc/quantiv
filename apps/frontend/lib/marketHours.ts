// US equity market-hours helpers.
//
// Two notions:
//   • NYSE regular session (for UI "market closed" badges): 09:30–16:00 ET.
//   • Quote refresh window (Finnhub cron + cache warming + fast client polls):
//     weekday 09:25 ET → 16:45 ET inclusive, excluding Sat/Sun and NYSE holidays.
// The pre-open buffer pre-warms before the bell. The post-close buffer (~45 min
// after the 16:00 bell) covers vendor delay before same-day close / day change
// show up in Finnhub. The cron walks a rotating cursor through ~300 symbols at
// ~50/min, so each ticker only updates every ~6 min; the 45-min buffer ensures
// every ticker gets at least one post-close refresh pass.
//
// Half-day sessions (e.g. 13:00 ET early close on Black Friday and Christmas
// Eve) are intentionally treated as full days. The harm is limited to a few
// hours of redundant Finnhub fetches that overwrite the same close price —
// not worth the two extra dates/year of calendar maintenance.

import { MARKET_HOLIDAYS_US } from './marketHolidays.generated';

/** First minute we start hitting Finnhub (slightly before the 09:30 open). */
const QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25; // 09:25 ET
/** Last minute we still refresh quotes (after 16:00 close; feeds often lag
 *  15–20+ min, and the rotating cursor takes a few minutes to reach every
 *  ticker, so 45 min ensures full post-close coverage). */
const QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45; // 16:45 ET

const REGULAR_OPEN_MIN = 9 * 60 + 30; // 09:30 ET
/** First minute after the 16:00 regular close (exclusive end for session-open check). */
const REGULAR_CLOSE_MIN = 16 * 60; // 16:00 ET

// Extended-hours windows for "tickers reporting today" focused refresh via
// Alpaca Basic's free IEX feed. IEX system hours are 08:00-17:00 ET, so
// the free feed is useful for the BMO window shortly before the open and
// the first post-close hour for AMC reporters. If we later pay for SIP,
// these bounds can widen toward the full 04:00-20:00 extended-hours span.
// The regular cron (above) walks the full universe inside RTH; these two
// narrower windows fire only for today's BMO / AMC reporters, which keeps
// Alpaca rate-limit pressure low.
//
// After-hours starts at 16:00 ET (the bell), not after the old regular
// cron's 16:45 settle. The first 15-45 minutes after the print is where
// most of the earnings move happens, so the refresh-session classifier
// gives after-hours precedence over the broader settle window below.
const PREMARKET_OPEN_MIN  = 8 * 60;       // 08:00 ET — start of IEX pre-market session
const PREMARKET_CLOSE_MIN = 9 * 60 + 24;  // 09:24 ET — hand off to the regular cron at 09:25
const AFTERHOURS_OPEN_MIN = 16 * 60;      // 16:00 ET — at the bell (earnings prints land here)
const AFTERHOURS_CLOSE_MIN = 17 * 60;     // 17:00 ET — end of IEX post-market session

const NYSE_HOLIDAYS: ReadonlySet<string> = new Set(MARKET_HOLIDAYS_US);

interface NowParts {
  weekday: string;
  minutes: number;
  isoDate: string; // YYYY-MM-DD in ET, for holiday lookup
}

function nowParts(d: Date = new Date()): NowParts {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
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
  if (NYSE_HOLIDAYS.has(isoDate)) return false;
  return true;
}

/** Returns today's date in ET as YYYY-MM-DD. Use this anywhere a route is
 *  filtering by "today" — server local time can disagree with ET around
 *  UTC midnight, e.g. 19:30 ET Monday is 00:30 UTC Tuesday during EST,
 *  and `new Date().toISOString().slice(0,10)` would pick Tuesday. */
export function etDateIso(now: Date = new Date()): string {
  return nowParts(now).isoDate;
}

/** NYSE regular session only — drives "market closed" UI while quotes may still refresh. */
export function isNyseRegularSessionET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes } = nowParts(now);
  return minutes >= REGULAR_OPEN_MIN && minutes < REGULAR_CLOSE_MIN;
}

/** True after the regular 16:00 ET close on the given ET date. */
export function hasRegularClosePassedET(now: Date = new Date()): boolean {
  const { minutes } = nowParts(now);
  return minutes >= REGULAR_CLOSE_MIN;
}

/** When cron / batch-price / fast polling should still hit Finnhub. */
export function isQuoteRefreshWindowET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes } = nowParts(now);
  return minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= QUOTE_REFRESH_CLOSE_MIN;
}

/** Pre-market window — Alpaca extended-hours quotes for BMO reporters
 *  reporting *today*. Window ends right before the regular cron starts
 *  so the two never compete for the same minute. */
export function isPremarketWindowET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes } = nowParts(now);
  return minutes >= PREMARKET_OPEN_MIN && minutes <= PREMARKET_CLOSE_MIN;
}

/** After-hours window — Alpaca extended-hours quotes for AMC reporters
 *  reporting *today*. Window starts at the regular 16:00 ET close. */
export function isAfterhoursWindowET(now: Date = new Date()): boolean {
  if (!isTradingDayET(now)) return false;
  const { minutes } = nowParts(now);
  return minutes >= AFTERHOURS_OPEN_MIN && minutes <= AFTERHOURS_CLOSE_MIN;
}

/** Classify the current minute into the matching window kind, or null
 *  if outside all windows. Used by the cron route to pick a refresh
 *  strategy without re-running every window check. */
export type RefreshWindowKind = 'premarket' | 'regular' | 'afterhours';

export function currentRefreshWindow(now: Date = new Date()): RefreshWindowKind | null {
  if (!isTradingDayET(now)) return null;
  const { minutes } = nowParts(now);
  if (minutes >= PREMARKET_OPEN_MIN  && minutes <= PREMARKET_CLOSE_MIN)  return 'premarket';
  // After-hours takes precedence over the broader quote-refresh settle
  // window so AMC reporters get Alpaca ticks immediately after 16:00 ET.
  if (minutes >= AFTERHOURS_OPEN_MIN && minutes <= AFTERHOURS_CLOSE_MIN) return 'afterhours';
  if (minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= QUOTE_REFRESH_CLOSE_MIN) return 'regular';
  return null;
}

/** @deprecated Prefer isNyseRegularSessionET or isQuoteRefreshWindowET for clarity. */
export function isMarketOpenET(now: Date = new Date()): boolean {
  return isQuoteRefreshWindowET(now);
}

/** Human-readable status string for UI badges. Returns null during regular hours. */
export function marketClosedReason(now: Date = new Date()): string | null {
  const { weekday, minutes, isoDate } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return 'Weekend · last close';
  if (NYSE_HOLIDAYS.has(isoDate)) return 'Market holiday · last close';
  if (minutes < REGULAR_OPEN_MIN) return 'Pre-market · last close';
  if (minutes >= REGULAR_CLOSE_MIN && minutes <= QUOTE_REFRESH_CLOSE_MIN) {
    return 'After close · quotes settling';
  }
  if (minutes > QUOTE_REFRESH_CLOSE_MIN) return 'After-hours · last close';
  return null;
}
