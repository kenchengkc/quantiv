import { expect, test } from '@playwright/test';
import { clerk } from '@clerk/testing/playwright';

test.describe('production controls (authenticated admin)', () => {
  test.skip(
    !process.env.E2E_CLERK_USER_EMAIL,
    'E2E_CLERK_USER_EMAIL not set — skipping authenticated specs.',
  );

  test('shows publication history and the latest release comparison', async ({
    page,
  }) => {
    await page.goto('/sign-in');
    await clerk.loaded({ page });
    await clerk.signIn({
      page,
      emailAddress: process.env.E2E_CLERK_USER_EMAIL!,
    });

    await page.goto('/ml-status');

    await expect(page).toHaveURL(/\/ml-status(\?.*)?$/);
    await expect(
      page.getByRole('heading', { name: 'Production Controls', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Publication history' }),
    ).toBeVisible();
    await expect(
      page.getByRole('img', { name: /Earnings coverage and eligible ATM pair availability across/ }),
    ).toBeVisible();
    await expect(page.getByText('Coverage change')).toBeVisible();
    await expect(page.getByText('Missing events', { exact: true })).toBeVisible();
    await expect(page.getByText('Recent release audit')).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Promotion' })).toBeVisible();

    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });
});
