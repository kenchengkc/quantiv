import { describe, it, expect } from 'vitest';
import { buildIntradayPayload } from './alpaca';

// Timestamps are UTC; June is EDT (UTC−4), so 19:55Z = 15:55 ET, 20:55Z = 16:55 ET.
describe('buildIntradayPayload', () => {
  it('anchors previousClose to the prior session REGULAR close, ignoring after-hours bars', () => {
    // ADBE-style AMC reporter: Thursday's regular session closes at 218.80,
    // then the 16:55 ET after-hours print has already dropped to 207.90 on the
    // earnings report. Friday's regular session settles at 204.78. The headline
    // anchor must be Thursday's REGULAR close (218.80) — anchoring to the
    // after-hours bar (207.90) would understate the reaction as ~-1.5%.
    const bars = [
      { t: '2026-06-11T19:30:00Z', c: 219.5 }, // Thu 15:30 ET (regular)
      { t: '2026-06-11T19:55:00Z', c: 218.8 }, // Thu 15:55 ET (regular close)
      { t: '2026-06-11T20:55:00Z', c: 207.9 }, // Thu 16:55 ET (AFTER-HOURS drop)
      { t: '2026-06-12T13:30:00Z', c: 205.1 }, // Fri 09:30 ET (open)
      { t: '2026-06-12T19:55:00Z', c: 204.78 }, // Fri 15:55 ET (close)
    ];
    const end = new Date('2026-06-13T12:00:00Z'); // Saturday — Friday is the latest session

    const payload = buildIntradayPayload(bars, end);

    expect(payload.sessionDate).toBe('2026-06-12');
    expect(payload.isCurrentSession).toBe(false);
    expect(payload.previousClose).toBe(218.8); // NOT the 207.90 after-hours bar
    // Full earnings reaction off the official close, ~-6.4% (not -1.5%).
    const pct = (204.78 - payload.previousClose!) / payload.previousClose!;
    expect(pct).toBeCloseTo(-0.0641, 4);
    // Session bars are Friday's regular session only.
    expect(payload.bars.map((b) => b.c)).toEqual([205.1, 204.78]);
  });

  it('returns a null payload when there are no bars', () => {
    const payload = buildIntradayPayload([], new Date('2026-06-13T12:00:00Z'));
    expect(payload).toEqual({
      bars: [],
      previousClose: null,
      asOf: null,
      sessionDate: null,
      isCurrentSession: false,
    });
  });

  it('marks a live session current when bars include today (ET)', () => {
    const bars = [
      { t: '2026-06-11T19:55:00Z', c: 218.8 }, // Thu 15:55 ET (regular close)
      { t: '2026-06-12T13:30:00Z', c: 205.1 }, // Fri 09:30 ET
      { t: '2026-06-12T17:00:00Z', c: 206.2 }, // Fri 13:00 ET (latest print)
    ];
    const end = new Date('2026-06-12T17:05:00Z'); // Friday 13:05 ET, mid-session

    const payload = buildIntradayPayload(bars, end);

    expect(payload.sessionDate).toBe('2026-06-12');
    expect(payload.isCurrentSession).toBe(true);
    expect(payload.previousClose).toBe(218.8);
  });
});
