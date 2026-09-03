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