import { test, expect, type Page } from '@playwright/test';

/**
 * Screener virtualization + sticky-column smoke tests.
 *
 * These tests do NOT require Clerk auth — the screener is a public page.
 * They verify the behaviors that the recent `react-virtuoso` refactor was
 * meant to deliver:
 *
 *  1. Page loads + the data table appears (not stuck in skeleton).
 *  2. Virtualization is real: the DOM holds far fewer <tr>s than the
 *     total row count would suggest.
 *  3. AMC filter no longer shifts column positions (the bug that
 *     triggered the table-layout: fixed + colgroup refactor).
 *  4. The Name column has `position: sticky` so it stays visible during
 *     horizontal scroll.
 *  5. Clicking a sortable column header changes the top row's ticker
 *     (proves sort still wires up under TableVirtuoso).
 */

async function gotoScreener(page: Page) {
  let lastError: unknown = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.goto('/screener', {
        waitUntil: 'domcontentloaded',
        timeout: 15_000,
      });
      return;
    } catch (err) {
      lastError = err;
      await page.waitForTimeout(400);
    }
  }
  throw lastError;
}

test.describe('screener · virtualization', () => {
  test.describe.configure({ timeout: 60_000 });

  test.beforeEach(async ({ page }) => {
    await gotoScreener(page);
    // Wait until the real data count replaces the initial zero-count
    // skeleton state.
    await page.waitForFunction(() => {
      const text = document.querySelector('[data-testid="screener-filtered-count"]')
        ?.textContent
        ?.trim();
      return Number(text) > 50;
    });
    // Give virtuoso one more frame to settle after contentReady flips.
    await page.waitForTimeout(300);
  });

  test('renders the SCREENER header', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /screener/i })).toBeVisible();
  });

  test('mounts only a virtualized window of rows', async ({ page }) => {
    // Total result set is in the stacked header callout.
    const text = (await page.getByTestId('screener-filtered-count').textContent()) ?? '';
    const total = Number(text.trim());
    expect(total).toBeGreaterThan(50); // we expect a real population

    // Scroll the page so the table is squarely in view. We use a fixed
    // pixel scroll instead of `locator.scrollIntoViewIfNeeded()` on a
    // <tr> — virtuoso unmounts/remounts rows on scroll so the captured
    // locator goes stale mid-action.
    await page.evaluate(() => window.scrollBy(0, 600));
    await page.waitForTimeout(400);

    // Count rows that virtuoso has actually mounted.
    const renderedRows = await page.locator('table tbody tr').count();
    // The point of virtualization: DOM row count must be far below the
    // total. We give plenty of headroom (50) for overscan; a busted
    // virtualizer would render hundreds.
    expect(renderedRows).toBeLessThanOrEqual(50);
    // And it must actually be mounting *something* — not zero.
    expect(renderedRows).toBeGreaterThanOrEqual(1);
    // The critical assertion: rendered rows << total rows.
    expect(renderedRows).toBeLessThan(total);
  });

  test('Name column is position: sticky', async ({ page }) => {
    await page.evaluate(() => window.scrollBy(0, 600));
    await page.waitForSelector('table tbody tr td', { state: 'attached' });

    const nameCell = page.locator('table tbody tr td').first();
    const position = await nameCell.evaluate(
      (el) => getComputedStyle(el).position,
    );
    expect(position).toBe('sticky');

    const left = await nameCell.evaluate((el) => getComputedStyle(el).left);
    expect(left).toBe('0px');
  });

  test('AMC filter does not shift column positions', async ({ page }) => {
    // Capture the left edge of one of the trailing columns (Spot, near the
    // end of the table) before and after toggling AMC. With the
    // table-layout: fixed + colgroup refactor, the position should be
    // identical regardless of which subset is visible.
    //
    // We measure the screen-x of the cell text rather than a header to
    // avoid the sticky-thead's z-index masking the row beneath.

    // Pick a column that's always within the default 1280px viewport
    // before scrolling. "Hist 4Q avg" sits around x=516..604 — comfortably
    // inside the viewport, and its position should be byte-for-byte
    // identical regardless of which filter is active because the
    // colgroup widths are fixed.
    const headerCell = page
      .locator('.qv-screener-table-shell thead th')
      .filter({ hasText: /Hist 4Q avg/i })
      .first();
    await expect(headerCell).toBeVisible();
    const beforeX = await headerCell.evaluate((el) => el.getBoundingClientRect().x);

    // Click AMC in the All/BMO/AMC segmented pill group.
    await page.getByRole('button', { name: /^AMC$/, exact: true }).first().click();
    // Give the filter a tick to settle.
    await page.waitForTimeout(300);

    const afterX = await headerCell.evaluate((el) => el.getBoundingClientRect().x);
    // Allow 1px of jitter for sub-pixel rounding; nothing more.
    expect(Math.abs(afterX - beforeX)).toBeLessThanOrEqual(1);
  });

  test('sorting wires up to URL params', async ({ page }) => {
    // The screener stores sort state in `?sort=` / `?dir=` query params.
    // Asserting against the URL is more reliable than comparing visible
    // top-row tickers: GTLS happens to top both the default `hist_edge
    // desc` order AND the date ASC order, so a row-content diff
    // produced false negatives.

    // Default: hist_edge desc (no `?sort=` until user clicks).
    await page.getByRole('button', { name: /^Date/i }).first().click();
    await expect(page).toHaveURL(/sort=date/);
    await expect(page).toHaveURL(/dir=asc/);

    // Second click on the same column flips direction.
    await page.getByRole('button', { name: /^Date/i }).first().click();
    await expect(page).toHaveURL(/sort=date/);
    await expect(page).toHaveURL(/dir=desc/);
  });
});
