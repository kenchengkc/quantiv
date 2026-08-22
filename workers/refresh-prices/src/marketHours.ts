// Mirrors apps/frontend/lib/marketHours.ts. The Worker can't import from
// the Next app (separate deploy target), so the constants and holiday
// set are duplicated here — keep both in sync when updating NYSE
// holidays or window bounds.
//
// DST is handled by `Intl.DateTimeFormat({ timeZone: 'America/New_York' })`
// below — it consults the IANA tz database at runtime, so the precise
// EDT↔EST transition dates (US DST: 2nd Sun of Mar → 1st Sun of Nov, so
// 2026-03-08, 2026-11-01, 2027-03-14, 2027-11-07, 2028-03-12, 2028-11-05)
// don't need to be hardcoded here.

const PREMARKET_OPEN_MIN = 8 * 60; // 08:00 ET (IEX pre-market start)
const PREMARKET_CLOSE_MIN = 9 * 60 + 24; // 09:24 ET
const QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25; // 09:25 ET
const QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45; // 16:45 ET
const AFTERHOURS_OPEN_MIN = 16 * 60; // 16:00 ET
const AFTERHOURS_CLOSE_MIN = 17 * 60; // 17:00 ET (IEX post-market end)

export type RefreshWindowKind = 'premarket' | 'regular' | 'afterhours';

// Keep aligned with apps/frontend/lib/marketHours.ts NYSE_HOLIDAYS.
export const WORKER_NYSE_HOLIDAYS = [
  // 2026
  '2026-01-01',
  '2026-01-19',
  '2026-02-16',
  '2026-04-03',
  '2026-05-25',
  '2026-06-19',
  '2026-07-03',
  '2026-09-07',
  '2026-11-26',
  '2026-12-25',
  // 2027
  '2027-01-01',
  '2027-01-18',
  '2027-02-15',
  '2027-03-26',
  '2027-05-31',
  '2027-06-18',
  '2027-07-05',
  '2027-09-06',
  '2027-11-25',
  '2027-12-24',
  // 2028
  '2028-01-17',
  '2028-02-21',
  '2028-04-14',
  '2028-05-29',
  '2028-06-19',
  '2028-07-04',
  '2028-09-04',
  '2028-11-23',
  '2028-12-25',
] as const;

const NYSE_HOLIDAYS: ReadonlySet<string> = new Set(WORKER_NYSE_HOLIDAYS);

function nowEtParts(d: Date): { weekday: string; minutes: number; isoDate: string } {
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
    minutes: ((parseInt(m.hour ?? '0') % 24) * 60) + parseInt(m.minute ?? '0'),
    isoDate: `${m.year ?? '0000'}-${m.month ?? '01'}-${m.day ?? '01'}`,
  };
}

export function currentRefreshWindow(d: Date): RefreshWindowKind | null {
  const { weekday, minutes, isoDate } = nowEtParts(d);
  if (weekday === 'Sat' || weekday === 'Sun') return null;
  if (NYSE_HOLIDAYS.has(isoDate)) return null;
  if (minutes >= PREMARKET_OPEN_MIN && minutes <= PREMARKET_CLOSE_MIN) return 'premarket';
  // After-hours takes precedence from the 16:00 bell so AMC reporters
  // get Alpaca extended-hours quotes during the most important window.
  if (minutes >= AFTERHOURS_OPEN_MIN && minutes <= AFTERHOURS_CLOSE_MIN) return 'afterhours';
  if (minutes >= QUOTE_REFRESH_OPEN_MIN && minutes <= QUOTE_REFRESH_CLOSE_MIN) return 'regular';
  return null;
}
