import { test, expect } from '@playwright/test';

/**
 * Earnings calendar (/) is public — no Clerk sign-in required.
 * Verifies REALIZED vs LIVE reaction labels and captures screenshots.
 */
test.describe('earnings calendar reaction labels', () => {
  test('past week shows OHLCV close-to-close moves as REALIZED', async ({ page }) => {
    // The default week comes from the real clock (mondayOf(new Date())), and the
    // realized reporters this test relies on (AZO 05-26, ADSK 05-28) live in the
    // 2026-05-25 week. Pin the clock into that week so the default `/` view is
    // deterministic regardless of when CI runs (otherwise it drifts forward and
    // the realized reporters fall into "Last week").
    await page.clock.setFixedTime(new Date('2026-05-29T21:00:00Z')); // Fri 17:00 ET
    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(2_000);

    await expect(page.getByText(/^REALIZED$/).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/^LIVE$/)).toHaveCount(0);
    await expect(page.getByRole('link', { name: /AZO/i }).first()).toContainText('8.99%');

    await page.screenshot({
      path: 'test-results/earnings-reaction/calendar-last-week.png',
      fullPage: true,
    });
  });

  test('shows REALIZED when weekly JSON includes realized_move_pct', async ({ page }) => {
    const patchWeek = async (route: import('@playwright/test').Route) => {
      const response = await route.fetch();
      const json = (await response.json()) as {
        events?: {
          ticker: string;
          earnings_date: string;
          timing?: string;
          realized_move_pct?: number | null;
          em_straddle_pct?: number;
        }[];
      };
      for (const e of json.events ?? []) {
        if (e.ticker === 'ADSK' && e.earnings_date.startsWith('2026-05-28')) {
          e.realized_move_pct = 0.0312;
          e.timing = 'after_market_close';
        }
      }
      await route.fulfill({ json });
    };

    await page.route('**/weeks/*.json', patchWeek);
    await page.route('**/weekly.json', patchWeek);

    // Pin into the 2026-05-25 week so the default view contains ADSK's 05-28
    // report (see the note in the first test).
    await page.clock.setFixedTime(new Date('2026-05-29T21:00:00Z')); // Fri 17:00 ET
    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: 'All' }).click();
    await page.waitForTimeout(2_000);

    await expect(page.getByText('REALIZED').first()).toBeVisible({ timeout: 15_000 });
    const adsk = page.getByRole('link', { name: /ADSK/i }).first();
    await expect(adsk).toContainText('3.12%');
    await page.screenshot({
      path: 'test-results/earnings-reaction/calendar-realized-mock.png',
      fullPage: true,
    });
  });

  test('shows LIVE for upcoming reporters when quotes are available', async ({ page }) => {
    await page.route('**/api/stocks/batch-price*', async (route) => {
      const url = new URL(route.request().url());
      const symbols = (url.searchParams.get('symbols') ?? '').split(',').filter(Boolean);
      await route.fulfill({
        json: {
          updated: new Date().toISOString(),
          source: 'finnhub',
          session: 'closed',
          marketOpen: false,
          quoteRefreshActive: false,
          pending: 0,
          data: symbols.map((symbol) => ({
            symbol,
            price: 100,
            previousClose: 98,
            change: 2,
            changePct: 0.0204,
          })),
        },
      });
    });

    await page.goto('/');
    await expect(page.locator('.qv-calendar-shell')).toBeVisible({ timeout: 60_000 });
    await page.locator('button.chip', { hasText: 'Next week' }).click();
    await page.getByRole('button', { name: 'All' }).click();
    await page.waitForTimeout(3_000);

    await expect(page.getByText('LIVE').first()).toBeVisible({ timeout: 20_000 });
    await page.screenshot({
      path: 'test-results/earnings-reaction/calendar-next-week-live.png',
      fullPage: true,
    });
  });
});
