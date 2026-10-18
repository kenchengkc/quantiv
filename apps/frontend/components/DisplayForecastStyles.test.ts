import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'components/DisplayForecast.module.css'), 'utf8');

function rule(name: string): string {
  const match = css.match(new RegExp(`\\.${name}\\s*\\{([^}]*)\\}`));
  return match?.[1] ?? '';
}

describe('calendar forecast source styling', () => {
  it('preserves stronger ML type and italic historical fallbacks', () => {
    const ml = rule('ml');
    const options = rule('options');
    const historical = rule('historical');
    const prior = rule('prior');

    expect(ml).toMatch(/font-weight:\s*7\d\d/);

    expect(options).not.toMatch(/font-style:\s*italic/);

    expect(historical).toMatch(/font-style:\s*italic/);

    expect(prior).toMatch(/font-style:\s*italic/);
  });
});
