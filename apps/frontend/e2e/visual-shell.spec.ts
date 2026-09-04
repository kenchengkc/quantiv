import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test';

const DESKTOP_WIDTHS = [1440, 1280, 1180, 1100, 1051];
const COMPACT_WIDTHS = [1050, 1024, 900, 768, 641, 640, 390, 360];
const PUBLIC_ROUTES = ['/', '/screener', '/research', '/validation', '/about', '/AVGO'];

test.beforeEach(async ({ page }) => {
  // This suite audits the rendered application shell, not the once-per-session
  // homepage intro. Skip the splash before first paint so it cannot obscure a
  // narrow-width screenshot or consume the shared viewport-matrix timeout.
  await page.addInitScript(() => {
    window.sessionStorage.setItem('quantiv:splash:played', '1');
  });
});

async function expectNoHorizontalOverflow(page: Page) {
  const metrics = await page.evaluate(() => ({
    viewport: window.innerWidth,
    rootScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
  }));

  expect(metrics.rootScrollWidth).toBeLessThanOrEqual(metrics.viewport + 1);
  expect(metrics.bodyScrollWidth).toBeLessThanOrEqual(metrics.viewport + 1);
}

async function attachShellScreenshot(page: Page, testInfo: TestInfo, name: string) {
  await testInfo.attach(name, {
    body: await page.screenshot({ fullPage: false }),
    contentType: 'image/png',
  });
}

async function expectTopbarHydrated(header: Locator) {
  // The wall clock starts hidden in the server render and becomes available
  // in Topbar's first client effect. This avoids clicking before React has
  // attached handlers without waiting on unrelated image requests.
  await expect(header.locator('.mono.tnum.qv-m-hide')).toHaveAttribute(
    'aria-hidden',
    'false',
  );
}

test('desktop navigation stays on one line throughout its supported width range', async ({ page }, testInfo) => {
  for (const width of DESKTOP_WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const header = page.locator('.quantiv-app-shell > header');
    await expectTopbarHydrated(header);
    const nav = header.locator('nav');
    const earnings = nav.getByRole('link', { name: 'Earnings Calendar' });
    const menuButton = header.getByRole('button', { name: 'Open menu' });

    await expect(nav).toBeVisible();
    await expect(earnings).toBeVisible();
    await expect(menuButton).toBeHidden();

    const textLayout = await earnings.evaluate((element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return {
        whiteSpace: style.whiteSpace,
        height: rect.height,
        lineHeight: Number.parseFloat(style.lineHeight),
      };
    });

    expect(textLayout.whiteSpace).toBe('nowrap');
    if (Number.isFinite(textLayout.lineHeight)) {
      expect(textLayout.height).toBeLessThan(textLayout.lineHeight * 1.6);
    }

    const headerBox = await header.boundingBox();
    expect(headerBox?.height ?? 0).toBeLessThan(80);
    await expectNoHorizontalOverflow(page);

    if (width === 1440 || width === 1100 || width === 1051) {
      await attachShellScreenshot(page, testInfo, `desktop-shell-${width}`);
    }
  }
});

test('compact navigation replaces the desktop menu before labels can crowd or wrap', async ({ page }, testInfo) => {
  for (const width of COMPACT_WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const header = page.locator('.quantiv-app-shell > header');
    await expectTopbarHydrated(header);
    const nav = header.locator('nav');
    const menuButton = header.getByRole('button', { name: 'Open menu' });

    await expect(nav).toBeHidden();
    await expect(menuButton).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await menuButton.click();
    await expect(header.getByRole('button', { name: 'Close menu' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    const mobileEarnings = header.getByRole('link', { name: 'Earnings Calendar' });
    const mobileValidation = header.getByRole('link', { name: 'Validation' });
    await expect(mobileEarnings).toBeVisible();
    await expect(mobileValidation).toBeVisible();

    const mobileLabel = await mobileEarnings.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return {
        height: rect.height,
        lineHeight: Number.parseFloat(style.lineHeight),
      };
    });
    if (Number.isFinite(mobileLabel.lineHeight)) {
      expect(mobileLabel.height).toBeLessThan(mobileLabel.lineHeight * 2.4);
    }

    await expectNoHorizontalOverflow(page);

    if (width === 1050 || width === 768 || width === 390) {
      await attachShellScreenshot(page, testInfo, `compact-shell-${width}`);
    }
  }
});

test('public pages keep research audit detail out of the global shell and remain viewport-clean', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });

  for (const route of PUBLIC_ROUTES) {
    await page.goto(route, { waitUntil: 'domcontentloaded' });

    await expect(
      page.getByRole('status', {
        name: 'Current Quantiv research evidence status',
      }),
    ).toHaveCount(0);

    await expectNoHorizontalOverflow(page);

    const headerBox = await page.locator('.quantiv-app-shell > header').boundingBox();
    expect(headerBox?.height ?? 0).toBeLessThan(80);

    await attachShellScreenshot(
      page,
      testInfo,
      `route-${route === '/' ? 'calendar' : route.slice(1).split('/').join('-')}`,
    );
  }
});
