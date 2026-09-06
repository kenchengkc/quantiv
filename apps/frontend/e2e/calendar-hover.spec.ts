import { expect, test, type Page } from '@playwright/test';

async function installCalendar(page: Page) {
  await page.clock.setFixedTime(new Date('2026-05-29T20:30:00Z'));
  await page.addInitScript(() => sessionStorage.setItem('quantiv:splash:played', '1'));
  const week = {
    metadata: { as_of_date: '2026-05-29', method: 'fixture' },
    window: { start: '2026-05-25', end: '2026-05-29' },
    events: [{ ticker: 'AVGO', earnings_date: '2026-05-28', timing: 'before_market_open',
      em_ml_pct: 0.07, em_straddle_pct: 0.13, p25: 0.04, p75: 0.11 }],
  };
  await page.route('**/weeks/*.json', (route) => route.fulfill({ json: route.request().url().includes('manifest')
    ? { current_week: '2026-05-25', as_of_date: '2026-05-29', weeks: [] } : week }));
  await page.route('**/weekly.json', (route) => route.fulfill({ json: week }));
  await page.route('**/api/stocks/batch-price*', (route) => route.fulfill({ json: {
    updated: '2026-05-29T20:30:00Z', marketOpen: false, quoteRefreshActive: false, pending: 0, data: [],
  } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  const row = page.locator('.qv-calendar-shell a[href="/AVGO"]');
  await expect(row).toContainText('7.0%');
  return row;
}

for (const width of [1440, 768, 390]) {
  test(`name and move popups are mutually exclusive at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const row = await installCalendar(page);
    const identity = row.locator('[data-calendar-identity]');
    const move = row.locator('[data-calendar-move]');
    const namePopup = page.getByRole('tooltip', { name: 'AVGO company information' });
    const movePopup = page.getByRole('tooltip', { name: 'Expected move breakdown' });

    // Direct entry over the move must not start the row's old 900ms name timer.
    await move.hover();
    await expect(movePopup).toBeVisible();
    await page.waitForTimeout(1100);
    await expect(namePopup).toHaveCount(0);
    await expect(movePopup).toContainText('ML');
    await expect(movePopup).toContainText('Implied');
    await expect(movePopup).toContainText('Typical');
    await testInfo.attach(`move-hover-${width}`, { body: await page.screenshot(), contentType: 'image/png' });

    // An already-visible name card must disappear when crossing to the move.
    await identity.hover();
    await expect(namePopup).toBeVisible();
    await expect(movePopup).toHaveCount(0);
    await move.hover();
    await expect(namePopup).toHaveCount(0);
    await expect(movePopup).toBeVisible();
    await page.waitForTimeout(1100);
    await expect(namePopup).toHaveCount(0);

    // Leaving the name before its delay expires must cancel the pending card.
    await identity.hover();
    await page.waitForTimeout(150);
    await move.hover();
    await page.waitForTimeout(1100);
    await expect(namePopup).toHaveCount(0);
    await expect(movePopup).toBeVisible();
    await page.mouse.move(1, 1);
    await expect(movePopup).toHaveCount(0);
    await expect(namePopup).toHaveCount(0);
    expect(await row.getAttribute('href')).toBe('/AVGO');
  });
}
