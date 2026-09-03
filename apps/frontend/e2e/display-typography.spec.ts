import { expect, test, type Locator } from '@playwright/test';

test.use({ viewport: { width: 1440, height: 900 } });

type StyleSnapshot = {
  fontFamily: string;
  fontFeatureSettings: string;
  fontSize: string;
  fontWeight: string;
  letterSpacing: string;
  lineHeight: string;
  textTransform: string;
};

async function styleOf(locator: Locator): Promise<StyleSnapshot> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      fontFamily: style.fontFamily,
      fontFeatureSettings: style.fontFeatureSettings,
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      letterSpacing: style.letterSpacing,
      lineHeight: style.lineHeight,
      textTransform: style.textTransform,
    };
  });
}

function expectMulishDisplay(style: StyleSnapshot) {
  expect(style.fontFamily.toLowerCase()).toContain('mulish');
  expect(style.fontFeatureSettings.toLowerCase()).toContain('ss01');
  const size = Number.parseFloat(style.fontSize);
  const tracking = Number.parseFloat(style.letterSpacing);
  expect(tracking / size).toBeCloseTo(-0.015, 3);
}

test('product hierarchy uses the restrained institutional Mulish system', async ({ page }) => {
  await page.goto('/about');

  const cardTitle = await styleOf(
    page.getByRole('heading', { name: 'What is priced?' }),
  );
  expectMulishDisplay(cardTitle);
  expect(cardTitle.fontSize).toBe('20px');
  expect(cardTitle.fontWeight).toBe('400');
  expect(cardTitle.lineHeight).toBe('22px');

  const sectionTitle = await styleOf(
    page.getByRole('heading', { name: 'See the research move.' }),
  );
  expectMulishDisplay(sectionTitle);
  expect(sectionTitle.fontSize).toBe('32px');
  expect(sectionTitle.fontWeight).toBe('600');

  await page.goto('/screener');
  const pageTitle = await styleOf(
    page.getByRole('heading', { name: /^screener$/i }).first(),
  );
  expectMulishDisplay(pageTitle);
  expect(pageTitle.fontSize).toBe('48px');
  expect(pageTitle.fontWeight).toBe('700');
  expect(pageTitle.textTransform).toBe('none');
});

test('legacy inline typography snaps to canonical rendered values', async ({ page }) => {
  await page.goto('/screener');

  const bodyStyles = await page.locator('body *').evaluateAll((elements) => {
    const visible = elements.filter((element) => {
      const html = element as HTMLElement;
      const style = getComputedStyle(html);
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        html.offsetWidth > 0 &&
        html.offsetHeight > 0 &&
        (html.textContent?.trim().length ?? 0) > 0
      );
    });

    return visible.map((element) => {
      const style = getComputedStyle(element);
      return {
        size: style.fontSize,
        weight: style.fontWeight,
        style: style.fontStyle,
      };
    });
  });

  const canonicalSizes = new Set(['10px', '12px', '14px', '16px', '20px', '32px', '48px', '64px']);
  const canonicalWeights = new Set(['400', '600', '700']);

  for (const typography of bodyStyles) {
    expect(canonicalSizes.has(typography.size)).toBe(true);
    expect(canonicalWeights.has(typography.weight)).toBe(true);
    expect(typography.style).toBe('normal');
  }
});
