import { describe, expect, it } from 'vitest';
import {
  SPLASH_SESSION_KEY,
  markSplashPlayed,
  shouldSkipSplash,
  splashAlreadyPlayed,
} from './splashSession';

describe('splashSession', () => {
  it('round-trips the played flag through sessionStorage', () => {
    window.sessionStorage.removeItem(SPLASH_SESSION_KEY);
    expect(splashAlreadyPlayed()).toBe(false);
    markSplashPlayed();
    expect(window.sessionStorage.getItem(SPLASH_SESSION_KEY)).toBe('1');
    expect(splashAlreadyPlayed()).toBe(true);
  });

  it('skips once the intro has played', () => {
    window.sessionStorage.removeItem(SPLASH_SESSION_KEY);
    window.matchMedia = (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    });
    expect(shouldSkipSplash()).toBe(false);
    markSplashPlayed();
    expect(shouldSkipSplash()).toBe(true);
  });
});
