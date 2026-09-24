import { expect, test } from '@playwright/test';

test('methodology formulas start open and toggle minus to plus', async ({ page }) => {
  await page.goto('/about#models-and-math');

  const disclosures = page.locator('#models-and-math ~ div details');
  await expect(disclosures).toHaveCount(7);

  for (let index = 0; index < 7; index += 1) {
    const disclosure = disclosures.nth(index);
    await expect(disclosure).toHaveAttribute('open', '');
    await expect(disclosure.locator('summary [data-disclosure-glyph]')).toHaveText('−');
  }

  const first = disclosures.first();
  await first.locator('summary').click();
  await expect(first).not.toHaveAttribute('open', '');
  await expect(first.locator('summary [data-disclosure-glyph]')).toHaveText('+');

  await first.locator('summary').click();
  await expect(first).toHaveAttribute('open', '');
  await expect(first.locator('summary [data-disclosure-glyph]')).toHaveText('−');
});
test('publication walkthrough explains both a released and a blocked candidate', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/about', { waitUntil: 'domcontentloaded' });
  const flow = page.getByRole('region', { name: 'Publication controls walkthrough' });
  await flow.scrollIntoViewIfNeeded();
  await expect(flow.getByRole('button', { name: 'Pause walkthrough' })).toHaveCount(0);
  await expect(flow.getByText('Illustrative example · not live status')).toBeVisible();
  await flow.getByRole('button', { name: '03 Verify' }).click();
  await expect(flow.getByRole('heading', { name: 'Forecast consistency' })).toBeVisible();
  await expect(flow.getByText('Calendar 4.8% = ticker 4.8%')).toBeVisible();
  await flow.getByRole('button', { name: '04 Publish' }).click();
  await expect(flow.getByText('New snapshot published')).toBeVisible();
  await flow.screenshot({ path: testInfo.outputPath('published-desktop.png') });
  await flow.getByRole('button', { name: 'Stale quote' }).click();
  const observe = flow.getByRole('button', { name: '01 Observe' });
  await observe.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(flow.getByText('Publication blocked', { exact: true })).toBeVisible();
  await expect(flow.getByText('Last validated release stays available', { exact: true })).toBeVisible();
  await expect(flow.getByRole('button', { name: '04 Publish' })).toBeDisabled();
  await flow.screenshot({ path: testInfo.outputPath('blocked-desktop.png') });
  await expect(flow.getByRole('link', { name: /View actual validation evidence/ })).toHaveAttribute('href', '/validation');
});

test('publication walkthrough fits a narrow screen', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/about', { waitUntil: 'domcontentloaded' });
  const flow = page.getByRole('region', { name: 'Publication controls walkthrough' });
  await flow.scrollIntoViewIfNeeded();
  await expect(flow.getByRole('button', { name: 'Pause walkthrough' })).toHaveCount(0);
  await flow.getByRole('button', { name: '04 Publish' }).click();
  await expect(flow.getByText('New snapshot published')).toBeVisible();
  expect(await flow.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await flow.screenshot({ path: testInfo.outputPath('published-mobile.png') });
});
