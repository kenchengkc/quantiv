/** Shared splash session flag — set once the intro has played in this tab. */
export const SPLASH_SESSION_KEY = 'quantiv:splash:played';
export const SPLASH_SKIP_ATTRIBUTE = 'data-quantiv-splash-skip';
export const SPLASH_FIRST_PAINT_ATTRIBUTE = 'data-quantiv-splash-first-paint';

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
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    return reduced || splashAlreadyPlayed();
  } catch {
    return true;
  }
}

export const SPLASH_FIRST_PAINT_COVER_ID = 'qv-fp-cover';

export function removeSplashFirstPaintCover(): void {
  if (typeof document === 'undefined') return;
  document.documentElement.removeAttribute(SPLASH_FIRST_PAINT_ATTRIBUTE);
  document.getElementById(SPLASH_FIRST_PAINT_COVER_ID)?.remove();
}

export function markSplashPlayed(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(SPLASH_SESSION_KEY, '1');
  } catch {
    // ignore
  }
}
