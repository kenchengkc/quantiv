// US equity market-hours helpers.
//
// We treat the regular session + a small pre/post buffer as "open":
//   weekday 09:25 ET → 16:15 ET inclusive.
// The buffer lets the cron pre-warm just before the bell and capture the
// final settlement just after close. Holidays are NOT enumerated — on the
// ~9 NYSE holidays per year we waste a few hundred Finnhub calls. Adding a
// holiday calendar is overkill until that quota becomes painful.

const OPEN_MIN = 9 * 60 + 25;   // 09:25 ET
const CLOSE_MIN = 16 * 60 + 15; // 16:15 ET

function nowParts(d: Date = new Date()): { weekday: string; minutes: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
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
  };
}

export function isMarketOpenET(now: Date = new Date()): boolean {
  const { weekday, minutes } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return false;
  return minutes >= OPEN_MIN && minutes <= CLOSE_MIN;
}

/** Human-readable status string for UI badges. Returns null when open. */
export function marketClosedReason(now: Date = new Date()): string | null {
  const { weekday, minutes } = nowParts(now);
  if (weekday === 'Sat' || weekday === 'Sun') return 'Weekend · last close';
  if (minutes < OPEN_MIN) return 'Pre-market · last close';
  if (minutes > CLOSE_MIN) return 'After-hours · last close';
  return null;
}
