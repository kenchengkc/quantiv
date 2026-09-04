import { expect, test } from '@playwright/test';

test('research validation exposes model, calibration, controls, and lineage', async ({ page }) => {
  await page.goto('/validation');

  await expect(
    page.getByRole('heading', { level: 1, name: /research validation/i }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /does the model add information/i }),
  ).toBeVisible();
  await expect(page.getByText('Relative MAE improvement')).toBeVisible();
  await expect(page.getByText('T-7')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /calibration/i }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /current research controls/i }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /evidence behind this page/i }),
  ).toBeVisible();
  await expect(page.getByText(/end-of-day research evidence/i)).toBeVisible();
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
