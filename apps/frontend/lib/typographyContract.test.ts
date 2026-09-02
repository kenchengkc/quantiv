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
    expect(css).toContain('--qv-type-section: 38px');
    expect(css).toContain('--qv-type-page-title: 56px');
    expect(css).toContain('--qv-type-data-display: 64px');
  });

  it('loads only the unified UI font and technical data font', () => {
    const layout = read('../app/layout.tsx');
    const globalsIndex = layout.indexOf("import './globals.css';");
    const typographyIndex = layout.indexOf("import './typography.css';");

    expect(globalsIndex).toBeGreaterThanOrEqual(0);
    expect(typographyIndex).toBeGreaterThan(globalsIndex);
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

  it('normalizes the reference About heading and primary page-title role', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('.qv-type-section-title');
    expect(css).toContain('body:has(.quantiv-hero-q) h2.serif');
    expect(css).toContain('body:has(.qv-screener-table-shell) h1.qv-m-h1');
    expect(css).toContain('font-size: var(--qv-type-section) !important');
    expect(css).toContain('font-size: var(--qv-type-page-title) !important');
  });
});