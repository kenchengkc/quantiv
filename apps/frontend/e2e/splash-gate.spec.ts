import { expect, test } from '@playwright/test';
import { SPLASH_SESSION_KEY } from '@/lib/splashSession';

test.describe('homepage splash gate', () => {
  test('first visit gates the calendar before hydration', async ({ page }) => {
    await page.addInitScript((key) => {
      sessionStorage.removeItem(key);
    }, SPLASH_SESSION_KEY);

    await page.goto('/', { waitUntil: 'commit' });

    await expect
      .poll(async () => page.evaluate(() => document.documentElement.getAttribute('data-splash-gate')))
      .toBe('1');

    const shellHidden = await page.evaluate(() => {
      const shell = document.querySelector('.quantiv-app-shell');
      if (!shell) return false;
      return getComputedStyle(shell).visibility === 'hidden';
    });
    expect(shellHidden).toBe(true);
  });

  test('returning session skips the gate', async ({ page }) => {
    await page.addInitScript((key) => {
      sessionStorage.setItem(key, '1');
    }, SPLASH_SESSION_KEY);

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const gate = await page.evaluate(() =>
      document.documentElement.getAttribute('data-splash-gate'),
    );
    expect(gate).toBeNull();

    await expect(page.locator('.quantiv-app-shell')).toBeVisible();
    const shellHidden = await page.evaluate(() => {
      const shell = document.querySelector('.quantiv-app-shell');
      if (!shell) return null;
      return getComputedStyle(shell).visibility === 'hidden';
    });
    expect(shellHidden).toBe(false);
  });
});
