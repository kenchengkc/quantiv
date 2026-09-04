import { expect, test } from '@playwright/test';

const PUBLIC_ROUTES = ['/', '/screener', '/about', '/AVGO'];

test('research evidence status is not duplicated across the global page shell', async ({ page }) => {
  for (const route of PUBLIC_ROUTES) {
    await page.goto(route);

    await expect(
      page.getByRole('status', {
        name: 'Current Quantiv research evidence status',
      }),
    ).toHaveCount(0);

    await expect(page.locator('header').getByRole('link', { name: 'Validation' })).toBeVisible();
  }
});

test('validation remains the dedicated research evidence surface', async ({ page }) => {
  await page.goto('/validation');

  await expect(
    page.getByRole('heading', { level: 1, name: /research validation/i }),
  ).toBeVisible();
  await expect(page.locator('header').getByRole('link', { name: 'Validation' })).toBeVisible();
});
