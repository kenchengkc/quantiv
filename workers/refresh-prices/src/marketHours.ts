import MARKET_SESSIONS from '../../../config/market_sessions.json';

// DST is handled by Intl with the contract's America/New_York timezone. Full
// holidays and early closes come from config/market_sessions.json; this worker
// no longer carries an independent exchange calendar.

const PREMARKET_OPEN_MIN = 8 * 60; // 08:00 ET (IEX pre-market start)
const PREMARKET_CLOSE_MIN = 9 * 60 + 24; // 09:24 ET
const QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25; // 09:25 ET
const QUOTE_SETTLE_MINUTES = 45;
const DEFAULT_REGULAR_CLOSE_MIN = 16 * 60; // 16:00 ET
const AFTERHOURS_CLOSE_MIN = 17 * 60; // 17:00 ET (IEX post-market end)

export type RefreshWindowKind = 'premarket' | 'regular' | 'afterhours';

export const WORKER_NYSE_HOLIDAYS: readonly string[] = MARKET_SESSIONS.holidays;
const NYSE_HOLIDAYS: ReadonlySet<string> = new Set(WORKER_NYSE_HOLIDAYS);
const EARLY_CLOSE_MINUTES: ReadonlyMap<string, number> = new Map(
  Object.entries(MARKET_SESSIONS.early_closes).map(([isoDate, close]) => {
    const [hour, minute] = close.split(':').map(Number);
    return [isoDate, hour * 60 + minute] as const;
  }),
);

function nowEtParts(d: Date): { weekday: string; minutes: number; isoDate: string } {
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
    minutes: ((parseInt(m.hour ?? '0') % 24) * 60) + parseInt(m.minute ?? '0'),
    isoDate: `${m.year ?? '0000'}-${m.month ?? '01'}-${m.day ?? '01'}`,
  };
}

function regularCloseMinute(isoDate: string): number {
  return EARLY_CLOSE_MINUTES.get(isoDate) ?? DEFAULT_REGULAR_CLOSE_MIN;
}

function quoteRefreshCloseMinute(isoDate: string): number {
  return regularCloseMinute(isoDate) + QUOTE_SETTLE_MINUTES;
}

export function currentRefreshWindow(d: Date): RefreshWindowKind | null {
  const { weekday, minutes, isoDate } = nowEtParts(d);
  if (weekday === 'Sat' || weekday === 'Sun') return null;
  if (NYSE_HOLIDAYS.has(isoDate)) return null;
  if (minutes >= PREMARKET_OPEN_MIN && minutes <= PREMARKET_CLOSE_MIN) return 'premarket';
  // After-hours takes precedence from the actual regular close, including NYSE
  // 13:00 early-close sessions.
  if (minutes >= regularCloseMinute(isoDate) && minutes <= AFTERHOURS_CLOSE_MIN) {
    return 'afterhours';
  }
  if (minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= quoteRefreshCloseMinute(isoDate)) {
    return 'regular';
  }
  return null;
}
