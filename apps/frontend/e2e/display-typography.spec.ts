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

  const heroTitle = await styleOf(
    page.getByRole('heading', { name: 'See what options imply.' }),
  );
  expectMulishDisplay(heroTitle);
  expect(heroTitle.fontSize).toBe('64px');
  expect(heroTitle.fontWeight).toBe('800');
  expect(heroTitle.textTransform).toBe('uppercase');

  const cardTitle = await styleOf(
    page.getByRole('heading', { name: 'What is priced?' }),
  );
  expectMulishDisplay(cardTitle);
  expect(cardTitle.fontSize).toBe('20px');
  expect(cardTitle.fontWeight).toBe('400');
  expect(cardTitle.lineHeight).toBe('22px');
  expect(cardTitle.textTransform).toBe('none');

  const sectionTitle = await styleOf(
    page.getByRole('heading', { name: 'See the research move.' }),
  );
  expectMulishDisplay(sectionTitle);
  expect(sectionTitle.fontSize).toBe('32px');
  expect(sectionTitle.fontWeight).toBe('600');
  expect(sectionTitle.textTransform).toBe('none');

  await page.goto('/screener');
  const pageTitle = await styleOf(
    page.getByRole('heading', { name: /^screener$/i }).first(),
  );
  expectMulishDisplay(pageTitle);
  expect(pageTitle.fontSize).toBe('48px');
  expect(pageTitle.fontWeight).toBe('800');
  expect(pageTitle.textTransform).toBe('uppercase');
});

test('legacy inline typography snaps to canonical rendered values', async ({ page }) => {
  test.slow();
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
      const html = element as HTMLElement;
      const style = getComputedStyle(element);
      return {
        tag: element.tagName.toLowerCase(),
        className: html.className?.toString().slice(0, 120) ?? '',
        text: html.textContent?.trim().replace(/\s+/g, ' ').slice(0, 100) ?? '',
        size: style.fontSize,
        weight: style.fontWeight,
        style: style.fontStyle,
      };
    });
  });

  const canonicalSizes = new Set(['10px', '12px', '14px', '16px', '20px', '32px', '48px', '64px']);
  const canonicalWeights = new Set(['400', '600', '800']);
  const invalid = bodyStyles.filter(
    (typography) =>
      !canonicalSizes.has(typography.size) ||
      !canonicalWeights.has(typography.weight) ||
      typography.style !== 'normal',
  );

  expect(invalid, JSON.stringify(invalid.slice(0, 20), null, 2)).toEqual([]);
});

test('ordinary text uses three neutral roles while signal colors stay semantic', async ({ page }) => {
  await page.goto('/screener');

  const colors = await page.evaluate(() => {
    const probe = (token: string) => {
      const element = document.createElement('span');
      element.style.color = `var(${token})`;
      element.textContent = token;
      document.body.appendChild(element);
      const color = getComputedStyle(element).color;
      element.remove();
      return color;
    };

    return {
      primary: probe('--qv-text-primary'),
      secondary: probe('--qv-text-secondary'),
      muted: probe('--qv-text-muted'),
      legacyMuted: probe('--ink-4'),
      accent: probe('--qv-text-accent'),
      positive: probe('--qv-text-positive'),
      negative: probe('--qv-text-negative'),
      warning: probe('--qv-text-warning'),
    };
  });

  expect(new Set([colors.primary, colors.secondary, colors.muted]).size).toBe(3);
  expect(colors.legacyMuted).toBe(colors.muted);

  const neutralColors = new Set([colors.primary, colors.secondary, colors.muted]);
  expect(neutralColors.has(colors.accent)).toBe(false);
  expect(neutralColors.has(colors.positive)).toBe(false);
  expect(neutralColors.has(colors.negative)).toBe(false);
  expect(neutralColors.has(colors.warning)).toBe(false);

  const navigation = page.getByRole('navigation');
  const activeNav = navigation.getByRole('link', { name: 'Screener' });
  const inactiveNav = navigation.getByRole('link', { name: 'About' });
  const helper = page
    .locator('span')
    .filter({ hasText: /Sortable table of every upcoming earnings print/ })
    .last();

  expect(await activeNav.evaluate((element) => getComputedStyle(element).color)).toBe(
    colors.primary,
  );
  expect(await inactiveNav.evaluate((element) => getComputedStyle(element).color)).toBe(
    colors.muted,
  );
  expect(await helper.evaluate((element) => getComputedStyle(element).color)).toBe(
    colors.muted,
  );

  await page.goto('/about');
  const aboutColors = await page.evaluate(() => {
    const probe = (token: string) => {
      const element = document.createElement('span');
      element.style.color = `var(${token})`;
      document.body.appendChild(element);
      const color = getComputedStyle(element).color;
      element.remove();
      return color;
    };
    return { primary: probe('--qv-text-primary'), accent: probe('--qv-text-accent') };
  });

  const cardTitle = page.getByRole('heading', { name: 'What is priced?' });
  const heroAccent = page.getByText('options imply.', { exact: true });
  const heroStyle = await heroAccent.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      color: style.color,
      textFill: style.getPropertyValue('-webkit-text-fill-color'),
    };
  });

  expect(await cardTitle.evaluate((element) => getComputedStyle(element).color)).toBe(
    aboutColors.primary,
  );
  expect(heroStyle.color).toBe(aboutColors.accent);
  expect(heroStyle.textFill).not.toBe('transparent');
  expect(heroStyle.textFill).not.toBe('rgba(0, 0, 0, 0)');
});
