/** Shared splash session flag — must match the inline beforeInteractive gate script. */
export const SPLASH_SESSION_KEY = 'quantiv:splash:played';

/** Runs beforeInteractive in layout.tsx — keeps first-time homepage visitors on
 *  black until React mounts the splash (SSR calendar HTML is gated via CSS). */
export const SPLASH_GATE_SCRIPT = `(function(){try{var p=location.pathname;if((p==="/"||p==="")&&sessionStorage.getItem("${SPLASH_SESSION_KEY}")!=="1"&&!window.matchMedia("(prefers-reduced-motion: reduce)").matches){document.documentElement.setAttribute("data-splash-gate","1");}}catch(e){}})();`;

export function splashAlreadyPlayed(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.sessionStorage.getItem(SPLASH_SESSION_KEY) === '1';
  } catch {
    return true;
  }
}

export function markSplashPlayed(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(SPLASH_SESSION_KEY, '1');
  } catch {
    // ignore
  }
}

export function clearSplashGate(): void {
  if (typeof document === 'undefined') return;
  document.documentElement.removeAttribute('data-splash-gate');
}
