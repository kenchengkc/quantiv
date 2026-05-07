import { test, expect } from '@playwright/test';
import { clerk } from '@clerk/testing/playwright';

// /watchlist is the only Clerk-protected route (see middleware.ts).
// This spec proves the bypass: visit a public page so Clerk loads, sign
// in via Backend API token (no UI interaction), then assert /watchlist
// renders authenticated content instead of redirecting to /sign-in.
test.describe('watchlist (authenticated)', () => {
  test.skip(
    !process.env.E2E_CLERK_USER_EMAIL,
    'E2E_CLERK_USER_EMAIL not set — skipping authenticated specs.',
  );

  test('reaches the watchlist page when signed in', async ({ page }) => {
    // Required: navigate to an unprotected page first so Clerk's client
    // SDK is loaded and ready to receive the testing token.
    await page.goto('/');
    await clerk.loaded({ page });

    await clerk.signIn({
      page,
      emailAddress: process.env.E2E_CLERK_USER_EMAIL!,
    });

    await page.goto('/watchlist');

    // If the bypass failed, Clerk redirects to /sign-in instead.
    await expect(page).toHaveURL(/\/watchlist(\?.*)?$/);

    // Detach @clerk/testing's FAPI route handler before the page tears
    // down. Clerk's SDK keeps polling for session state in the background;
    // when Playwright closes the page, an in-flight retry hits the route
    // handler against a closed context and logs a noisy "Test ended"
    // error. Unrouting here drains the handler cleanly.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });
});

test('public earnings calendar renders without auth', async ({ page }) => {
  // Sanity check: most of the app is reachable without Clerk at all.
  await page.goto('/');
  await expect(page).toHaveURL(/\/(\?.*)?$/);
});
