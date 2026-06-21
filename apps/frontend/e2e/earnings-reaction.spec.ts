import { test, expect, type Route } from '@playwright/test';

/**
 * Earnings calendar (/) is public — no Clerk sign-in required.
 *
 * The first two tests verify the REALIZED-vs-LIVE *rendering logic* against
 * self-contained fixtures (inline week JSON + a pinned clock), so they are
 * deterministic regardless of when CI runs or how the committed data churns.
 * The last is a loose smoke test against the real build to catch data-shape /
 * render regressions without asserting on any specific dated reporter.
 */

// Pinned into the 2026-05-25 trading week. The calendar derives its default
// week from the real clock (mondayOf(new Date())); pinning makes "This week"
// deterministically 2026-05-25 so the fixtures below load by default.
const PINNED = new Date('2026-05-29T20:30:00Z'); // Fri 16:30 ET — IEX after-hours still live
const WEEK_START = '2026-05-25';
const WEEK_END = '2026-05-29';

type FixtureEvent = {
  ticker: string;
  earnings_date: string;
  timing: string;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  em_ml_pct?: number | null;
  p25?: number | null;
  p75?: number | null;
  realized_move_pct?: number | null;
};

function weekPayload(events: FixtureEvent[]) {
  return {
    metadata: { as_of_date: WEEK_END, method: 'fixture' },
    window: { start: WEEK_START, end: WEEK_END },
    events,
  };
}

/** Serve the inline week fixture for every weeks/weekly request; keep a valid
 *  (empty) manifest so anything that reads it doesn't choke. */
async function routeWeeks(route: Route, events: FixtureEvent[]) {
  const url = route.request().url();
  if (url.includes('manifest')) {
    await route.fulfill({
      json: { current_week: WEEK_START, as_of_date: WEEK_END, weeks: [] },
    });
    return;
  }
  await route.fulfill({ json: weekPayload(events) });
}

async function installWeekFixture(
  page: import('@playwright/test').Page,
  events: FixtureEvent[],
) {
  await page.clock.setFixedTime(PINNED);
  await page.route('**/weeks/*.json', (route) => routeWeeks(route, events));
  await page.route('**/weekly.json', (route) => routeWeeks(route, events));
}

test.describe('earnings calendar reaction labels', () => {
  test('renders REALIZED with the close-to-close move for a past reporter', async ({
    page,
  }) => {
    // AZO reported 05-27 AMC → realization window completes 05-28 close, which
    // is before the pinned 05-29, so the row must read REALIZED (-8.99%).
    await installWeekFixture(page, [
      {
        ticker: 'AZO',
        earnings_date: '2026-05-27',
        timing: 'after_market_close',
        em_straddle_pct: 0.05,
        realized_move_pct: -0.0899,
      },
    ]);

    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: 'All' }).click();
    await page.waitForTimeout(1_000);

    await expect(page.getByText(/^REALIZED$/).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/^LIVE$/)).toHaveCount(0);
    await expect(page.getByRole('link', { name: /AZO/i }).first()).toContainText('8.99%');
  });

  test('renders LIVE from a live quote for a not-yet-realized reporter', async ({
    page,
  }) => {
    // ZZZ reports 05-29 AMC → window completes on the next trading day (06-01),
    // after the pinned 05-29, so it is still live and should read LIVE.
    await installWeekFixture(page, [
      {
        ticker: 'ZZZ',
        earnings_date: '2026-05-29',
        timing: 'after_market_close',
        em_straddle_pct: 0.05,
        realized_move_pct: null,
      },
    ]);
    await page.route('**/api/stocks/batch-price*', async (route) => {
      const symbols = (new URL(route.request().url()).searchParams.get('symbols') ?? '')
        .split(',')
        .filter(Boolean);
      await route.fulfill({
        json: {
          updated: PINNED.toISOString(),
          source: 'finnhub',
          session: 'afterhours',
          marketOpen: false,
          quoteRefreshActive: true,
          pending: 0,
          data: symbols.map((symbol) => ({
            symbol,
            price: 110,
            previousClose: 100,
            change: 10,
            changePct: 0.1,
          })),
        },
      });
    });

    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: 'All' }).click();
    await page.waitForTimeout(2_000);

    await expect(page.getByText(/^LIVE$/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/^REALIZED$/)).toHaveCount(0);
    await expect(page.getByRole('link', { name: /ZZZ/i }).first()).toBeVisible();
  });

  test('headline ± uses the calibrated ML move + p25–p75 band, not the straddle', async ({
    page,
  }) => {
    // CALB has both an inflated straddle (±13%) and a calibrated ML move (±7%)
    // with a 50% interquartile range of 4–11%. The grid must show the ML move and
    // the IQR band, matching the screener/symbol surfaces — never the raw straddle.
    await installWeekFixture(page, [
      {
        ticker: 'CALB',
        earnings_date: '2026-05-28',
        timing: 'before_market_open',
        em_straddle_pct: 0.13,
        em_ml_pct: 0.07,
        p25: 0.04,
        p75: 0.11,
        realized_move_pct: null,
      },
    ]);

    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: 'All' }).click();
    await page.waitForTimeout(1_000);

    const row = page.getByRole('link', { name: /CALB/i }).first();
    await expect(row).toContainText('7.0%');        // calibrated ML move
    await expect(row).toContainText('4.0–11.0%');   // p25–p75 IQR band
    await expect(row).not.toContainText('13.0%');   // never the raw straddle
  });

  test('smoke: real calendar renders the shell and week navigation', async ({ page }) => {
    // No mocks: exercises the real build output. Intentionally asserts nothing
    // about specific tickers, dates, values, or reaction labels (those depend
    // on the live clock + data churn and are covered deterministically above),
    // so it only fails on a genuinely broken build or render.
    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'This week' })).toBeVisible();
    await page.getByRole('button', { name: 'All' }).click();
    await page.waitForTimeout(2_000);
    await page.screenshot({
      path: 'test-results/earnings-reaction/calendar-smoke.png',
      fullPage: true,
    });
  });
});
