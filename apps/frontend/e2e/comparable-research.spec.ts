import { expect, test } from '@playwright/test';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

function currentComparableEvent() {
  const directory = join(__dirname, '..', 'public', 'symbols');
  for (const filename of readdirSync(directory).sort()) {
    if (!filename.endsWith('.json')) continue;
    const payload = JSON.parse(readFileSync(join(directory, filename), 'utf8'));
    const implied = payload.expected_move?.straddle_pct;
    const timing = payload.expected_move?.timing ?? payload.next_earnings_timing;
    const session = ['after_market_close', 'amc'].includes(timing) ? 'amc'
      : ['before_market_open', 'bmo'].includes(timing) ? 'bmo' : null;
    if (typeof implied === 'number' && Number.isFinite(implied) && implied > 0 && session) {
      return { symbol: filename.slice(0, -5), implied, session };
    }
  }
  throw new Error('Published symbol corpus has no positive implied move with a known session');
}

// This path intentionally starts from a real event-specific symbol payload so
// the test proves the current static research state—not a live quote overlay—
// is what seeds the comparable historical cohort.
test('ticker research links into a comparable historical cohort', async ({ page }) => {
  // The daily release can retire any particular symbol's current forecast.
  // Select from the same disk corpus consumed by the server-rendered page;
  // browser request mocks cannot replace that server-side input.
  const event = currentComparableEvent();
  await page.goto(`/${event.symbol}`);

  const link = page.getByRole('link', { name: /comparable history/i });
  await expect(link).toBeVisible();
  const href = await link.getAttribute('href');
  expect(href).toContain('/research?');
  const query = new URL(href!, 'https://quantiv.example').searchParams;
  expect(query.get('timing')).toBe(event.session);
  expect(query.get('minImplied')).toBe(String(Number((event.implied * 0.75).toFixed(6))));
  expect(query.get('maxImplied')).toBe(String(Number((event.implied * 1.25).toFixed(6))));
  expect(query.get('sort')).toBe('ratio');

  const calibration = page.getByLabel('Comparable historical calibration summary');
  await expect(calibration).toBeVisible();
  await expect(calibration).toContainText('events across');
  await expect(calibration).toContainText('median realized/implied');
  await expect(calibration).toContainText('outside implied');

  await link.click();
  await expect(page).toHaveURL(/\/research\?/);
  await expect(page.getByRole('heading', { level: 1, name: /research lab/i })).toBeVisible();
  await expect(page.getByLabel('Session')).toHaveValue(event.session);
  await expect(page.getByLabel('Sort')).toHaveValue('ratio');
  await expect(page.getByText('Calibration map')).toBeVisible();
});
