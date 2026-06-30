import { describe, expect, it } from 'vitest';
import { SPLASH_SESSION_KEY, markSplashPlayed, splashAlreadyPlayed } from './splashSession';

describe('splashSession', () => {
  it('round-trips the played flag through sessionStorage', () => {
    window.sessionStorage.removeItem(SPLASH_SESSION_KEY);
    expect(splashAlreadyPlayed()).toBe(false);
    markSplashPlayed();
    expect(window.sessionStorage.getItem(SPLASH_SESSION_KEY)).toBe('1');
    expect(splashAlreadyPlayed()).toBe(true);
  });
});
