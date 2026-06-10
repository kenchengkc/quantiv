import { describe, expect, it } from 'vitest';
import { SPLASH_GATE_SCRIPT, SPLASH_SESSION_KEY } from './splashSession';

describe('splashSession', () => {
  it('gate script references the shared session key', () => {
    expect(SPLASH_GATE_SCRIPT).toContain(SPLASH_SESSION_KEY);
    expect(SPLASH_GATE_SCRIPT).toContain('data-splash-gate');
  });
});
