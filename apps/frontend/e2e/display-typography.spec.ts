import { expect, test, type Locator } from '@playwright/test';

test.use({ viewport: { width: 1440, height: 900 } });

type StyleSnapshot = {
  fontFamily: string;
  fontFeatureSettings: string;
  fontSize: string;
  fontWeight: string;
  letterSpacing: string;
  lineHeight: string;
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
    };
  });
}

function expectMulishDisplay(style: StyleSnapshot) {
  expect(style.fontFamily.toLowerCase()).toContain('mulish');
  expect(style.fontFeatureSettings.toLowerCase()).toContain('ss01');
  expect(parseFloat(style.letterSpacing)).toBeLessThan(0);
}

test('large product headings share the About-page Mulish display voice', async ({ page }) => {
  await page.goto('/about');

  const cardTitle = await styleOf(
    page.getByRole('heading', { name: 'What is priced?' }),
  );
  expectMulishDisplay(cardTitle);
  expect(cardTitle.fontSize).toBe('20px');
  expect(cardTitle.fontWeight).toBe('700');

  const sectionTitle = await styleOf(
    page.getByRole('heading', { name: 'See the research move.' }),
  );
  expectMulishDisplay(sectionTitle);
  expect(sectionTitle.fontSize).toBe('38px');
  expect(sectionTitle.fontWeight).toBe('700');

  await page.goto('/screener');
  const pageTitle = await styleOf(
    page.getByRole('heading', { name: /^screener$/i }).first(),
  );
  expectMulishDisplay(pageTitle);
  expect(pageTitle.fontSize).toBe('56px');
  expect(pageTitle.fontWeight).toBe('800');
});
