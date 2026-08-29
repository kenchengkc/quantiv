import { test, expect } from '@playwright/test';
import { clerk } from '@clerk/testing/playwright';

// Authenticated routes own the Clerk browser boundary; public routes do not
// load the SDK. This spec signs in from /sign-in, then proves /watchlist
// renders authenticated content instead of redirecting back there.
test.describe('watchlist (authenticated)', () => {
  test.skip(
    !process.env.E2E_CLERK_USER_EMAIL,
    'E2E_CLERK_USER_EMAIL not set — skipping authenticated specs.',
  );

  test('reaches the watchlist page when signed in', async ({ page }) => {
    // Required: navigate to an authenticated route first so Clerk's client
    // SDK is loaded and ready to receive the testing token.
    await page.goto('/sign-in');
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
