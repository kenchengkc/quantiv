import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'components/DisplayForecast.module.css'), 'utf8');

function rule(name: string): string {
  const match = css.match(new RegExp(`\\.${name}\\s*\\{([^}]*)\\}`));
  return match?.[1] ?? '';
}

describe('calendar forecast source styling', () => {
  it('makes ML visually primary and historical fallbacks visibly distinct without badges', () => {
    const ml = rule('ml');
    const options = rule('options');
    const historical = rule('historical');
    const prior = rule('prior');

    expect(ml).toContain('var(--brand-blue-1)');
    expect(ml).toMatch(/font-weight:\s*7\d\d/);

    expect(options).toContain('var(--brand-blue-1)');
    expect(options).not.toMatch(/font-style:\s*italic/);

    expect(historical).toContain('var(--ink-3)');
    expect(historical).toMatch(/font-style:\s*italic/);

    expect(prior).toContain('var(--ink-4)');
    expect(prior).toMatch(/font-style:\s*italic/);
  });
});
