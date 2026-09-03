import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

function read(relativePath: string) {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8');
}

describe('Quantiv typography contract', () => {
  it('uses Mulish for product UI and JetBrains Mono only for technical data', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('--qv-font-heading: var(--font-mulish)');
    expect(css).toContain('--qv-font-body: var(--font-mulish)');
    expect(css).toContain('--qv-font-data: var(--font-jetbrains-mono)');
    expect(css).not.toContain('font-nunito-sans');
  });

  it('keeps the visual scale to eight canonical size steps', () => {
    const css = read('../app/typography.css');
    const declarations = [...css.matchAll(/--qv-type-[\w-]+:\s*(\d+)px;/g)].map(
      (match) => Number(match[1]),
    );

    expect([...new Set(declarations)].sort((a, b) => a - b)).toEqual([
      10, 12, 14, 16, 20, 32, 48, 64,
    ]);
    expect(css).toContain('--qv-type-subhead: var(--qv-type-card-title)');
    expect(css).toContain('--qv-type-section: var(--qv-type-stat)');
    expect(css).toContain('--qv-type-page-title: var(--qv-type-detail-title)');
    expect(css).toContain('--qv-type-hero: var(--qv-type-data-display)');
  });

  it('limits weight, tracking, and line-height systems', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('--qv-weight-regular: 400');
    expect(css).toContain('--qv-weight-medium: 600');
    expect(css).toContain('--qv-weight-strong: 700');
    expect(css).toContain('--qv-track-normal: 0');
    expect(css).toContain('--qv-track-display: -0.015em');
    expect(css).toContain('--qv-track-label: 0.12em');
    expect(css).toContain('--qv-leading-tight: 1.05');
    expect(css).toContain('--qv-leading-heading: 1.1');
    expect(css).toContain('--qv-leading-ui: 1.35');
    expect(css).toContain('--qv-leading-body: 1.5');
  });

  it('keeps ordinary text to three neutral colors and reserves color for semantics', () => {
    const colors = read('../app/text-colors.css');

    expect(colors).toContain('--qv-text-primary: var(--ink)');
    expect(colors).toContain('--qv-text-secondary: var(--ink-2)');
    expect(colors).toContain('--qv-text-muted: var(--ink-3)');
    expect(colors).toContain('--ink-4: var(--qv-text-muted)');
    expect(colors).toContain('--qv-text-accent: var(--accent)');
    expect(colors).toContain('--qv-text-positive: var(--up)');
    expect(colors).toContain('--qv-text-negative: var(--down)');
    expect(colors).toContain('--qv-text-warning: var(--flag)');
  });

  it('loads unified typography and text colors after the legacy global layer', () => {
    const layout = read('../app/layout.tsx');
    const globalsIndex = layout.indexOf("import './globals.css';");
    const typographyIndex = layout.indexOf("import './typography.css';");
    const colorsIndex = layout.indexOf("import './text-colors.css';");

    expect(globalsIndex).toBeGreaterThanOrEqual(0);
    expect(typographyIndex).toBeGreaterThan(globalsIndex);
    expect(colorsIndex).toBeGreaterThan(typographyIndex);
    expect(layout).toContain('JetBrains_Mono, Mulish');
    expect(layout).not.toContain('Nunito_Sans');
    expect(layout).not.toContain('font-nunito-sans');
  });

  it('keeps Tailwind and Clerk on the same Mulish product family', () => {
    const tailwind = read('../tailwind.config.js');
    const authenticatedLayout = read('../app/(authenticated)/layout.tsx');

    expect(tailwind).not.toContain("'Inter'");
    expect(tailwind).not.toContain('font-nunito-sans');
    expect(tailwind).toContain("'var(--font-mulish)'");
    expect(tailwind).toContain("'var(--font-jetbrains-mono)'");
    expect(authenticatedLayout).toContain('var(--font-mulish)');
    expect(authenticatedLayout).not.toContain('font-nunito-sans');
  });

  it('uses restrained institutional hierarchy instead of page-specific display styles', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('h2:not(.qv-type-section-title),');
    expect(css).toContain('font-size: var(--qv-type-card-title) !important');
    expect(css).toContain('font-weight: var(--qv-weight-regular) !important');
    expect(css).toContain('.qv-type-section-title {');
    expect(css).toContain('font-weight: var(--qv-weight-medium) !important');
    expect(css).toContain('h1.qv-m-h1:not(.qv-week-heading)');
    expect(css).toContain('font-weight: var(--qv-weight-strong) !important');
    expect(css).toContain('text-transform: none !important');
  });

  it('snaps legacy inline typography onto the canonical grid', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('Legacy inline-style bridge.');
    expect(css).toContain('[style*="font-size: 9.5px"]');
    expect(css).toContain('[style*="font-size: 11.5px"]');
    expect(css).toContain('[style*="font-size: 15.5px"]');
    expect(css).toContain('[style*="font-weight: 650"]');
    expect(css).toContain('[style*="font-weight: 800"]');
    expect(css).toContain('[style*="font-style: italic"]');
  });
});
