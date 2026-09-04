import { expect, test } from '@playwright/test';

test('public routes expose current research evidence state', async ({ page }) => {
  await page.goto('/screener');

  const status = page.getByRole('status', {
    name: 'Current Quantiv research evidence status',
  });
  await expect(status).toBeVisible();
  await expect(status.getByText(/Research (passed|degraded|failed|unavailable)/i)).toBeVisible();
  await expect(status.getByText(/Snapshot /i)).toBeVisible();
  await expect(status.getByRole('link', { name: /audit evidence/i })).toHaveAttribute(
    'href',
    '/validation',
  );
});
