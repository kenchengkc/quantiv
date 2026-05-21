import { test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const LIVE = 'http://localhost:3000/screener';
const DESIGN = 'http://localhost:8088/Quantiv%20Redesign.html';

async function inspect(page: import('@playwright/test').Page, url: string, label: string) {
  await page.goto(url);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1500);

  if (url === DESIGN) {
    await page.evaluate(() => {
      const nav = Array.from(document.querySelectorAll('button, a'));
      const screener = nav.find((n) => /screener/i.test(n.textContent ?? ''));
      if (screener) (screener as HTMLElement).click();
    });
    await page.waitForTimeout(800);
  }

  // Check which fonts actually loaded.
  const loaded = await page.evaluate(() => {
    const checks = [
      ['Mulish 400', '400 16px Mulish'],
      ['Mulish 700', '700 16px Mulish'],
      ['Mulish 800', '800 16px Mulish'],
      ['Mulish 900', '900 16px Mulish'],
      ['Nunito Sans 400', '400 16px "Nunito Sans"'],
      ['Nunito Sans 600', '600 16px "Nunito Sans"'],
    ];
    return Object.fromEntries(
      checks.map(([label, spec]) => [label, document.fonts.check(spec)]),
    );
  });

  // Identify the actual rendered font for the SCREENER h1 by reading
  // off any matched font face entries.
  const usedFont = await page.evaluate(() => {
    const headings = Array.from(document.querySelectorAll('h1'));
    const h = headings.find((x) => /screener/i.test(x.textContent ?? ''));
    if (!h) return { error: 'no h1 found' };
    const cs = getComputedStyle(h);
    // Use Range to measure the glyph width itself, not the H1 block.
    // h.getBoundingClientRect() returns the H1's *container* width (block
    // element fills its flex parent), so it's useless for "is the text
    // rendering at the same size on both pages?". Range.getBoundingClientRect
    // gives the actual rendered text width.
    const range = document.createRange();
    range.selectNodeContents(h);
    const textRect = range.getBoundingClientRect().toJSON();
    range.detach?.();
    // Also measure with the canvas API as a cross-check.
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d')!;
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const canvasWidth = ctx.measureText((h.textContent ?? '').toUpperCase()).width;
    return {
      declared: cs.fontFamily,
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
      letterSpacing: cs.letterSpacing,
      fontStretch: cs.fontStretch,
      fontVariationSettings: cs.fontVariationSettings,
      fontFeatureSettings: cs.fontFeatureSettings,
      blockRect: h.getBoundingClientRect().toJSON(),
      textRect, // actual rendered glyph bbox
      canvasMeasuredWidth: canvasWidth,
      devicePixelRatio: window.devicePixelRatio,
      // Enumerate every @font-face the browser actually has registered,
      // so we can compare Mulish 800 source URLs between live and design.
      fontFaces: Array.from(document.fonts).map((f) => ({
        family: f.family,
        weight: f.weight,
        style: f.style,
        stretch: f.stretch,
        unicodeRange: f.unicodeRange,
        status: f.status,
      })),
    };
  });
  // Also capture every CSS stylesheet URL the page pulled in, so we can
  // spot extra @font-face injections (Next/Font, Tailwind preset, etc.)
  const stylesheets = await page.evaluate(() =>
    Array.from(document.styleSheets).map((s) => {
      try {
        return { href: s.href, length: s.cssRules?.length ?? 0 };
      } catch {
        return { href: s.href, length: 'CORS' };
      }
    }),
  );
  (usedFont as Record<string, unknown>).stylesheets = stylesheets;

  return { label, loaded, usedFont };
}

test('font loading audit', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const livePage = await ctx.newPage();
  const designPage = await ctx.newPage();

  const live = await inspect(livePage, LIVE, 'live');
  const design = await inspect(designPage, DESIGN, 'design');

  const out = { live, design };
  const dir = path.resolve(__dirname, '../test-results');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'font-loaded.json'),
    JSON.stringify(out, null, 2),
  );

  // Tight crops just on the SCREENER text so we can eyeball the glyphs.
  // Use the H1's coordinates but clip to a FIXED 400×80 window so both
  // images are the same size when viewed side by side — h1.screenshot()
  // grabs the bounding box, which is wider on the design page because
  // the right-side panel doesn't take as much horizontal room.
  for (const [page, name] of [
    [livePage, 'live'],
    [designPage, 'design'],
  ] as const) {
    const h1 = page.getByRole('heading', { name: /^screener$/i }).first();
    const rect = await h1.boundingBox();
    if (!rect) continue;
    await page.screenshot({
      path: path.join(dir, `${name}-screener-h1.png`),
      clip: {
        x: Math.round(rect.x) - 2,
        y: Math.round(rect.y) - 4,
        width: 400,
        height: 80,
      },
    });
    // Wider crop showing the heading + body line together, matching the
    // user's side-by-side comparison (heading on top, body text below).
    await page.screenshot({
      path: path.join(dir, `${name}-screener-block.png`),
      clip: {
        x: Math.max(0, Math.round(rect.x) - 30),
        y: Math.max(0, Math.round(rect.y) - 20),
        width: 1100,
        height: 260,
      },
    });
  }
});
