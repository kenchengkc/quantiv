// @vitest-environment node
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { readCalendarTypicalRanges } from './calendarTypicalRange.server';

const root = mkdtempSync(join(tmpdir(), 'calendar-ranges-'));
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });

it('reads each event’s published cutoff across week navigation and omits missing histories', () => {
  vi.spyOn(process, 'cwd').mockReturnValue(root);
  const write = (parts: string[], value: unknown) => {
    const path = join(root, 'public', ...parts);
    mkdirSync(join(path, '..'), { recursive: true });
    writeFileSync(path, JSON.stringify(value));
  };
  const current = { ticker: 'ABC', earnings_date: '2026-04-10', timing: 'bmo', display_forecast_as_of: '2026-04-08' };
  const next = { ticker: 'XYZ', earnings_date: '2026-04-17', timing: 'amc', display_forecast_as_of: '2026-04-09' };
  const initial = {
    metadata: { as_of_date: '2026-04-09', method: 'fixture' },
    window: { start: '2026-04-06', end: '2026-04-10' }, events: [current],
  };
  write(['weeks', '2026-04-13.json'], { ...initial, events: [next] });
  const history = { earnings_history: [
    { date: '2025-07-10', actual: 0.02 },
    { date: '2025-10-10', actual: 0.06 },
    { date: '2026-04-09', actual: 0.9 },
  ].map((row) => ({ ...row, timing: 'bmo', realized_target_protocol: 'quantiv.session-reaction.v2', realized_label_source: 'ohlcv_session_close' })) };
  write(['symbols', 'ABC.json'], history);
  write(['symbols', 'XYZ.json'], history);
  // XYZ is outside the supplied reference but remains reachable in week navigation.
  const ranges = readCalendarTypicalRanges([current, { ...next, ticker: 'MISSING' }], initial);
  expect(Object.keys(ranges)).toEqual(['ABC|2026-04-10', 'XYZ|2026-04-17']);
  expect(ranges['ABC|2026-04-10']).toMatchObject({ count: 2, asOf: '2026-04-08' });
  expect(ranges['ABC|2026-04-10'].high).toBeCloseTo(0.05);
  expect(ranges['XYZ|2026-04-17']).toMatchObject({ count: 3, asOf: '2026-04-09' });
  expect(ranges['XYZ|2026-04-17'].high).toBeCloseTo(0.48);
});
