// US equity market-hours helpers.
//
// "Open" = regular session + a small pre/post buffer:
//   weekday 09:25 ET → 16:15 ET inclusive,
//   excluding Sat/Sun and full NYSE holidays.
// The buffer lets the cron pre-warm just before the bell and capture the
// final settlement just after close.
//
// Half-day sessions (e.g. 13:00 ET early close on Black Friday and Christmas
// Eve) are intentionally treated as full days. The harm is limited to a few
// hours of redundant Finnhub fetches that overwrite the same close price —
// not worth the two extra dates/year of calendar maintenance.

const OPEN_MIN = 9 * 60 + 25;   // 09:25 ET
const CLOSE_MIN = 16 * 60 + 15; // 16:15 ET

// Full NYSE closures, ISO dates in ET. Sources: nyse.com/markets/hours-calendars.
// Update annually — appending a new year is fine; old years can stay (irrelevant
// dates simply never match the current ET date).
const NYSE_HOLIDAYS: ReadonlySet<string> = new Set([
  // 2026
  '2026-01-01', // New Year's Day
  '2026-01-19', // Martin Luther King Jr. Day
  '2026-02-16', // Presidents' Day
  '2026-04-03', // Good Friday
  '2026-05-25', // Memorial Day
  '2026-06-19', // Juneteenth
  '2026-07-03', // Independence Day (observed; July 4 is Saturday)
  '2026-09-07', // Labor Day
  '2026-11-26', // Thanksgiving
  '2026-12-25', // Christmas Day
  // 2027
  '2027-01-01', // New Year's Day
  '2027-01-18', // Martin Luther King Jr. Day
  '2027-02-15', // Presidents' Day
  '2027-03-26', // Good Friday
  '2027-05-31', // Memorial Day
  '2027-06-18', // Juneteenth (observed; June 19 is Saturday)
  '2027-07-05', // Independence Day (observed; July 4 is Sunday)
  '2027-09-06', // Labor Day
  '2027-11-25', // Thanksgiving
  '2027-12-24', // Christmas Day (observed; Dec 25 is Saturday)
  // 2028
  '2028-01-17', // Martin Luther King Jr. Day  (Jan 1 is Saturday — no observance)
  '2028-02-21', // Presidents' Day
  '2028-04-14', // Good Friday
  '2028-05-29', // Memorial Day
  '2028-06-19', // Juneteenth
  '2028-07-04', // Independence Day
  '2028-09-04', // Labor Day
  '2028-11-23', // Thanksgiving
  '2028-12-25', // Christmas Day
]);

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

export function isMarketOpenET(now: Date = new Date()): boolean {
  const { weekday, minutes, isoDate } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return false;
  if (NYSE_HOLIDAYS.has(isoDate)) return false;
  return minutes >= OPEN_MIN && minutes <= CLOSE_MIN;
}

/** Human-readable status string for UI badges. Returns null when open. */
export function marketClosedReason(now: Date = new Date()): string | null {
  const { weekday, minutes, isoDate } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return 'Weekend · last close';
  if (NYSE_HOLIDAYS.has(isoDate)) return 'Market holiday · last close';
  if (minutes < OPEN_MIN) return 'Pre-market · last close';
  if (minutes > CLOSE_MIN) return 'After-hours · last close';
  return null;
}
