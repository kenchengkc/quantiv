import { expect, test, type Route } from '@playwright/test';
import { installCalendarReference } from './helpers/calendar-fixture';

for (const firstBatch of ['success', 'failure', 'delayed']) {
  test(`calendar shows quotes beyond 400 tickers with another batch ${firstBatch}`, async ({ page }) => {
    await page.clock.setFixedTime(new Date('2026-10-26T15:00:00Z'));
    await page.addInitScript(() => sessionStorage.setItem('quantiv:splash:played', '1'));
    // Hundreds of synthetic tickers must not fan out to external logo providers.
    await page.route('**/*', (route) => route.request().resourceType() === 'image'
      ? route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"/>' })
      : route.fallback());
    const tickers = Array.from({ length: 400 }, (_, index) =>
      `Q${String.fromCharCode(65 + Math.floor(index / 26))}${String.fromCharCode(65 + index % 26)}`,
    ).concat('TSLA');
    const week = {
      metadata: { as_of_date: '2026-10-23', method: 'fixture' },
      window: { start: '2026-10-26', end: '2026-10-30' },
      events: tickers.map((ticker) => ({
        ticker, earnings_date: '2026-10-29', timing: 'after_market_close',
        em_ml_pct: 0.05, display_forecast_pct: 0.05, display_forecast_method: 'ml',
        ml_publication_status: 'eligible', ml_status: 'available',
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

    // The Popular view still requests all 401 weekly tickers, just like production.
    // Wait for hydration and the fixture request before timing the render assertion.
    const lastQuoteResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/stocks/batch-price' &&
        url.searchParams.get('symbols') === 'TSLA';
    });
    await page.goto('/');
    await lastQuoteResponse;
    const lateRow = page.locator('.qv-calendar-shell a[href="/TSLA"]');
    await expect(lateRow).toContainText('5.0%');
    await expect(lateRow).toContainText('1.23%LIVE');
    expect(batches.every((symbols) => symbols.length <= 400)).toBe(true);
    // The initial server-rendered week can start its own request before the fixture loads.
    expect(new Set(batches.flat().filter((symbol) => tickers.includes(symbol))).size).toBe(401);
    for (const route of delayedRoutes) await route.abort();
  });
}
