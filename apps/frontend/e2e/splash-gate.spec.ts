import { expect, test } from '@playwright/test';
import {
  SPLASH_FIRST_PAINT_ATTRIBUTE,
  SPLASH_SESSION_KEY,
  SPLASH_SKIP_ATTRIBUTE,
} from '@/lib/splashSession';

test.describe('homepage splash cover', () => {
  test('first visit covers the homepage from the first rendered frame', async ({ page }) => {
    const observedKey = 'quantiv:test:splash-first-paint-observed';

    // Simulate a brand-new session (splash not yet played).
    await page.addInitScript(
      ({ attribute, observedKey, sessionKey }) => {
        sessionStorage.removeItem(sessionKey);
        sessionStorage.removeItem(observedKey);

        const recordFirstPaintGuard = () => {
          if (document.documentElement?.getAttribute(attribute) === '1') {
            sessionStorage.setItem(observedKey, '1');
          }
        };

        new MutationObserver(recordFirstPaintGuard).observe(document, {
          attributes: true,
          childList: true,
          subtree: true,
        });
        recordFirstPaintGuard();
      },
      {
        attribute: SPLASH_FIRST_PAINT_ATTRIBUTE,
        observedKey,
        sessionKey: SPLASH_SESSION_KEY,
      },
    );

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    expect(await page.evaluate((key) => sessionStorage.getItem(key), observedKey)).toBe('1');
    await expect(page.locator('head > script#quantiv-splash-first-paint')).toHaveCount(1);
    await expect(page.locator('.quantiv-splash')).toBeVisible();

    // The shell still server-renders visibly; the splash is an overlay, not the
    // old visibility:hidden gate that removed the calendar from paint/layout.
    await expect(page.locator('.quantiv-app-shell')).toBeVisible();
    const shellHidden = await page.evaluate(() => {
      const shell = document.querySelector('.quantiv-app-shell');
      return shell ? getComputedStyle(shell).visibility === 'hidden' : null;
    });
    expect(shellHidden).toBe(false);
  });

  test('returning session also renders the shell immediately', async ({ page }) => {
    await page.addInitScript((key) => {
      sessionStorage.setItem(key, '1');
    }, SPLASH_SESSION_KEY);

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const skip = await page.evaluate((attr) =>
      document.documentElement.getAttribute(attr),
      SPLASH_SKIP_ATTRIBUTE,
    );
    expect(skip).toBe('1');

    await expect(page.locator('.quantiv-splash')).toBeHidden();
    await expect(page.locator('.quantiv-app-shell')).toBeVisible();
    const shellHidden = await page.evaluate(() => {
      const shell = document.querySelector('.quantiv-app-shell');
      return shell ? getComputedStyle(shell).visibility === 'hidden' : null;
    });
    expect(shellHidden).toBe(false);
  });

  test('subpages do not receive homepage splash markup', async ({ page }) => {
    await page.addInitScript((key) => {
      sessionStorage.removeItem(key);
    }, SPLASH_SESSION_KEY);

    await page.goto('/screener', { waitUntil: 'domcontentloaded' });

    await expect(page.locator('.quantiv-splash')).toHaveCount(0);
    await expect(page.locator('.quantiv-app-shell')).toBeVisible();
  });
});
