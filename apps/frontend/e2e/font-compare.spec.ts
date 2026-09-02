import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Visual + computed-style comparison between the live screener and the
 * design bundle's static HTML. Both should render "SCREENER" with the
 * same font-family + size + weight. If the user is seeing a different
 * font, this test surfaces the exact mismatch.
 */

const LIVE = 'http://localhost:3000/screener';
const DESIGN = 'http://localhost:8088/Quantiv%20Redesign.html';

async function capture(page: import('@playwright/test').Page, url: string) {
  await page.goto(url);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800); // give fonts time to swap in
  // Find the SCREENER heading. On the design page the visible h1
  // depends on initial navigation; we'd need to click the Screener tab
  // first.
  if (url === DESIGN) {
    // The design SPA mounts the calendar by default; jump to screener.
    await page.evaluate(() => {
      const nav = Array.from(document.querySelectorAll('button, a'));
      const screener = nav.find((n) => /screener/i.test(n.textContent ?? ''));
      if (screener) (screener as HTMLElement).click();
    });
    await page.waitForTimeout(800);
  }
  const heading = page.getByRole('heading', { name: /^screener$/i }).first();
  await expect(heading).toBeVisible();
  const computed = await heading.evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      fontFamily: cs.fontFamily,
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
      letterSpacing: cs.letterSpacing,
      lineHeight: cs.lineHeight,
      textTransform: cs.textTransform,
      color: cs.color,
    };
  });
  return { computed, page };
}

test('SCREENER headline computed styles match design', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const livePage = await ctx.newPage();
  const designPage = await ctx.newPage();

  const live = await capture(livePage, LIVE);
  const design = await capture(designPage, DESIGN);

  const out = {
    live: live.computed,
    design: design.computed,
  };
  // Write to disk so we can read the diff after the test run.
  const dir = path.resolve(__dirname, '../test-results');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'font-compare.json'),
    JSON.stringify(out, null, 2),
  );
  // Screenshots side-by-side for visual eyeballing.
  await livePage.screenshot({
    path: path.join(dir, 'live-screener.png'),
    fullPage: false,
    clip: { x: 0, y: 0, width: 1440, height: 600 },
  });
  await designPage.screenshot({
    path: path.join(dir, 'design-screener.png'),
    fullPage: false,
    clip: { x: 0, y: 0, width: 1440, height: 600 },
  });

  // Loose comparison: font-family should resolve to the same primary
  // typeface. Browsers report font-family as the raw stack (for example,
  // `Mulish, ui-sans-serif, system-ui, sans-serif`); we just check the first
  // family name matches.
  const livePrimary = live.computed.fontFamily.split(',')[0].replace(/['"]/g, '').trim();
  const designPrimary = design.computed.fontFamily.split(',')[0].replace(/['"]/g, '').trim();
  expect(livePrimary, `live=${livePrimary} vs design=${designPrimary}`).toBe(designPrimary);

  expect(live.computed.fontSize).toBe(design.computed.fontSize);
  expect(live.computed.fontWeight).toBe(design.computed.fontWeight);
  expect(live.computed.textTransform).toBe(design.computed.textTransform);
});