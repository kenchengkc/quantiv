import { expect, test } from '@playwright/test';

test('research validation presents a clear evidence hierarchy', async ({ page }) => {
  await page.goto('/validation');

  await expect(
    page.getByRole('heading', { level: 1, name: /research validation/i }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /does the model add information/i }),
  ).toBeVisible();
  await expect(page.getByText('Weighted error reduction')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Calibration', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /can new research publish/i }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /how the result is earned/i }),
  ).toBeVisible();

  const horizonDetails = page.locator('details').filter({
    has: page.getByText('Performance by horizon', { exact: true }),
  });
  await expect(horizonDetails).not.toHaveAttribute('open', '');

  const audit = page.locator('details').filter({
    has: page.getByText('Evidence behind this page', { exact: true }),
  });
  await expect(audit).not.toHaveAttribute('open', '');
  await audit.locator('summary').click();
  await expect(page.getByText(/end-of-day research evidence/i)).toBeVisible();

  await expect(
    page.getByRole('heading', { name: 'Published forecast evidence' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Validation protocol' }),
  ).toHaveCount(0);
});

test('validation is a first-class primary navigation route', async ({ page }) => {
  await page.goto('/');

  const validationLink = page.getByRole('link', { name: 'Validation' }).first();
  await expect(validationLink).toBeVisible();
  await validationLink.click();

  await expect(page).toHaveURL(/\/validation$/);
  await expect(
    page.getByRole('heading', { level: 1, name: /research validation/i }),
  ).toBeVisible();
});
