/** Shared splash session flag — set once the intro has played in this tab. */
export const SPLASH_SESSION_KEY = 'quantiv:splash:played';
export const SPLASH_SKIP_ATTRIBUTE = 'data-quantiv-splash-skip';

export function splashAlreadyPlayed(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.sessionStorage.getItem(SPLASH_SESSION_KEY) === '1';
  } catch {
    return true;
  }
}

export function shouldSkipSplash(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return (
      window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
      splashAlreadyPlayed()
    );
  } catch {
    return true;
  }
}

export function markSplashPlayed(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(SPLASH_SESSION_KEY, '1');
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
}
