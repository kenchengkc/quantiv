import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

function read(relativePath: string) {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8');
}

describe('Quantiv typography contract', () => {
  it('defines the three product font roles and the shared hierarchy scale', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('--qv-font-heading: var(--font-mulish)');
    expect(css).toContain('--qv-font-body: var(--font-nunito-sans)');
    expect(css).toContain('--qv-font-data: var(--font-jetbrains-mono)');
    expect(css).toContain('--qv-type-section: 38px');
    expect(css).toContain('--qv-type-page-title: 56px');
    expect(css).toContain('--qv-type-data-display: 64px');
  });

  it('loads the typography layer after the legacy global stylesheet', () => {
    const layout = read('../app/layout.tsx');
    const globalsIndex = layout.indexOf("import './globals.css';");
    const typographyIndex = layout.indexOf("import './typography.css';");

    expect(globalsIndex).toBeGreaterThanOrEqual(0);
    expect(typographyIndex).toBeGreaterThan(globalsIndex);
  });

  it('keeps Tailwind and Clerk on the same product families', () => {
    const tailwind = read('../tailwind.config.js');
    const authenticatedLayout = read('../app/(authenticated)/layout.tsx');

    expect(tailwind).not.toContain("'Inter'");
    expect(tailwind).toContain("'var(--font-nunito-sans)'");
    expect(tailwind).toContain("'var(--font-mulish)'");
    expect(tailwind).toContain("'var(--font-jetbrains-mono)'");
    expect(authenticatedLayout).toContain('var(--font-nunito-sans)');
  });

  it('normalizes the reference About heading and primary page-title role', () => {
    const css = read('../app/typography.css');

    expect(css).toContain('body:has(.quantiv-hero-q) h2.serif');
    expect(css).toContain('body:has(.qv-screener-table-shell) h1.qv-m-h1');
    expect(css).toContain('font-size: var(--qv-type-section) !important');
    expect(css).toContain('font-size: var(--qv-type-page-title) !important');
  });
});
