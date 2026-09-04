import { expect, test } from '@playwright/test';

// This path intentionally starts from a real event-specific symbol payload so
// the test proves the current static research state—not a live quote overlay—
// is what seeds the comparable historical cohort.
test('ticker research links into a comparable historical cohort', async ({ page }) => {
  await page.goto('/AVGO');

  const link = page.getByRole('link', { name: /comparable history/i });
  await expect(link).toBeVisible();
  const href = await link.getAttribute('href');
  expect(href).toContain('/research?');
  expect(href).toContain('timing=amc');
  expect(href).toContain('minImplied=0.07365');
  expect(href).toContain('maxImplied=0.12275');
  expect(href).toContain('minLead=0');
  expect(href).toContain('maxLead=1');
  expect(href).toContain('sort=ratio');

  const calibration = page.getByLabel('Comparable historical calibration summary');
  await expect(calibration).toBeVisible();
  await expect(calibration).toContainText('T0–1');
  await expect(calibration).toContainText('obs');
  await expect(calibration).toContainText('med');
  await expect(calibration).toContainText('outside');

  await link.click();
  await expect(page).toHaveURL(/\/research\?/);
  await expect(page).toHaveURL(/minLead=0/);
  await expect(page).toHaveURL(/maxLead=1/);
  await expect(page.getByRole('heading', { level: 1, name: /research lab/i })).toBeVisible();
  await expect(page.getByLabel('Session')).toHaveValue('amc');
  await expect(page.getByLabel('Min lead days')).toHaveValue('0');
  await expect(page.getByLabel('Max lead days')).toHaveValue('1');
  await expect(page.getByLabel('Sort')).toHaveValue('ratio');
  await expect(page.getByText('Calibration map')).toBeVisible();
});
