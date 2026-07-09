import { expect, test } from '@playwright/test';
import { SPLASH_SESSION_KEY } from '@/lib/splashSession';

// The intro splash no longer gates content. The calendar is server-rendered and
// paints immediately; the splash animates over it after hydration. These tests
// guard that a content-blocking gate (the old `data-splash-gate` attribute +
// `visibility: hidden` app-shell rule) never creeps back and re-delays first
// paint — the regression the SSR/LCP fix was about.
test.describe('homepage splash (content is never gated)', () => {
  test('first visit does not hide the app shell', async ({ page }) => {
    // Simulate a brand-new session (splash not yet played).
    await page.addInitScript((key) => {
      sessionStorage.removeItem(key);
    }, SPLASH_SESSION_KEY);

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // The removed gate attribute must not come back.
    const gate = await page.evaluate(() =>
      document.documentElement.getAttribute('data-splash-gate'),
    );
    expect(gate).toBeNull();

    // The app shell renders visible from first paint — not visibility:hidden.
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

    const gate = await page.evaluate(() =>
      document.documentElement.getAttribute('data-splash-gate'),
    );
    expect(gate).toBeNull();

    await expect(page.locator('.quantiv-app-shell')).toBeVisible();
    const shellHidden = await page.evaluate(() => {
      const shell = document.querySelector('.quantiv-app-shell');
      return shell ? getComputedStyle(shell).visibility === 'hidden' : null;
    });
    expect(shellHidden).toBe(false);
  });
});
