import { expect, test, type Route } from '@playwright/test';
import { installCalendarReference } from './helpers/calendar-fixture';

for (const firstBatch of ['success', 'failure', 'delayed']) {
  test(`calendar shows quotes beyond 400 tickers with another batch ${firstBatch}`, async ({ page }) => {
    await page.clock.setFixedTime(new Date('2026-10-26T15:00:00Z'));
    await page.addInitScript(() => sessionStorage.setItem('quantiv:splash:played', '1'));
    const tickers = Array.from({ length: 400 }, (_, index) =>
      `Q${String.fromCharCode(65 + Math.floor(index / 26))}${String.fromCharCode(65 + index % 26)}`,
    ).concat('QTEST');
    const week = {
      metadata: { as_of_date: '2026-10-23', method: 'fixture' },
      window: { start: '2026-10-26', end: '2026-10-30' },
      events: tickers.map((ticker) => ({
        ticker, earnings_date: '2026-10-29', timing: 'after_market_close',
        em_ml_pct: 0.05, display_forecast_pct: 0.05, display_forecast_method: 'ml',
      })),
    };
    await page.route('**/weeks/*.json', (route) => route.fulfill({ json:
      route.request().url().includes('manifest')
        ? { current_week: '2026-10-26', as_of_date: '2026-10-23', weeks: [] }
        : week,
    }));
    await page.route('**/weekly.json', (route) => route.fulfill({ json: week }));
    await installCalendarReference(page, week);
    const batches: string[][] = [];
    const delayedRoutes: Route[] = [];
    await page.route('**/api/stocks/batch-price*', (route) => {
      const symbols = new URL(route.request().url()).searchParams.get('symbols')!.split(',');
      batches.push(symbols);
      if (firstBatch === 'delayed' && symbols.includes('QAA')) {
        delayedRoutes.push(route);
        return;
      }
      if (firstBatch === 'failure' && symbols.includes('QAA')) {
        return route.fulfill({ status: 503, json: { error: 'temporary quote outage' } });
      }
      // Model the real endpoint's limit so an oversized request loses the last ticker.
      return route.fulfill({ json: {
        marketOpen: true, quoteRefreshActive: true, pending: 0,
        data: symbols.slice(0, 400).map((symbol) => ({
          symbol, price: 101.23, change: 1.23, changePct: 0.0123,
        })),
      } });
    });

    await page.goto('/?filter=all');
    const lateRow = page.locator('.qv-calendar-shell a[href="/QTEST"]');
    await expect(lateRow).toContainText('1.23%LIVE');
    if (firstBatch === 'success') {
      await expect(page.locator('.qv-calendar-shell a[href="/QAA"]')).toContainText('1.23%LIVE');
    }
    expect(batches.every((symbols) => symbols.length <= 400)).toBe(true);
    // The initial server-rendered week can start its own request before the fixture loads.
    expect(new Set(batches.flat().filter((symbol) => tickers.includes(symbol))).size).toBe(401);
    for (const route of delayedRoutes) await route.abort();
  });
}
