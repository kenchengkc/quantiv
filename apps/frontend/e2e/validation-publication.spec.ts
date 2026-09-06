import { expect, test } from '@playwright/test';
import control from '../public/control-plane.json';
import forecast from '../public/evidence/forecast.json';

function dateLabel(value: string) {
  return new Date(value).toLocaleString('en-US', {
    month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'America/New_York',
  });
}

test('validation separates current assessment from retained forecast evidence', async ({ page }) => {
  await page.goto('/validation');
  const publication = page.getByRole('region', { name: 'Research publication and freshness' });
  await expect(publication.getByText(`Last forecast validation: ${dateLabel(forecast.validated_at)} ET`, { exact: true })).toBeVisible();
  await expect(publication.getByText(`Latest assessment: ${dateLabel(control.generated_at)} ET`, { exact: true })).toBeVisible();
  await expect(publication.getByText(`Active options snapshot: ${control.data.source_date} EOD`, { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Current research status', exact: true })).toHaveCount(0);
  const performance = await page.getByRole('heading', { name: 'Does the model add information?' }).boundingBox();
  const calibration = await page.getByRole('heading', { name: 'Calibration', exact: true }).boundingBox();
  const controls = await publication.boundingBox();
  expect(performance).not.toBeNull();
  expect(calibration).not.toBeNull();
  expect(controls).not.toBeNull();
  expect(performance!.y).toBeLessThan(calibration!.y);
  expect(calibration!.y).toBeLessThan(controls!.y);
});

for (const width of [1440, 768, 390]) {
  test(`validation evidence is readable without clipped content at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/validation');
    await expect(page.getByRole('heading', { name: /research validation/i, level: 1 })).toBeVisible();
    const overflow = await page.evaluate(() => ({
      viewport: innerWidth, root: document.documentElement.scrollWidth, body: document.body.scrollWidth,
    }));
    expect(overflow.root).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.body).toBeLessThanOrEqual(overflow.viewport + 1);
    // Check individual evidence text too: overflow:hidden must not mask clipping.
    const lineage = page.getByRole('heading', { name: 'Evidence behind this page' }).locator('..').locator('..');
    const clipped = await lineage.locator('span').evaluateAll((nodes) => nodes.filter((node) => {
      const rect = node.getBoundingClientRect();
      return rect.left < -1 || rect.right > innerWidth + 1 || node.scrollWidth > node.clientWidth + 1;
    }).map((node) => node.textContent));
    expect(clipped).toEqual([]);
    await testInfo.attach(`validation-${width}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  });
}
