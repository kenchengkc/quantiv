'use client';

import { useEffect, useLayoutEffect, useState } from 'react';
import { usePathname } from 'next/navigation';

// Once-per-session intro. The sequence starts with a closed ring, opens the
// bottom-right slit, then reveals the exact logo tail through it.
const SESSION_KEY = 'quantiv:splash:played';
const TOTAL_MS = 2327;
const READY_HOLD_MS = 280;
const MAX_ASSET_WAIT_MS = 900;
const SPLASH_ASSETS = [
  '/brand/QuantivSplashQClosed.png',
  '/brand/QuantivSplashTail.png',
];

function wait(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function decodeSplashAsset(src: string) {
  const img = new Image();
  img.decoding = 'async';
  img.src = src;
  if (img.complete) return;
  try {
    await img.decode();
  } catch {
    // A decode failure should not trap the user on a black screen. The
    // normal <img> below will still make a best effort to render.
  }
}

export function Splash() {
  // Splash is a homepage-only intro. On any subpage we don't render it at
  // all — including during SSR — so reopening a tab (Cmd+Shift+T) to
  // /screener, /watchlist, /AAPL, etc. doesn't flash the white ring for a
  // frame during hydration. (The flash came from the SSR HTML including
  // the splash because `useState` initial values run on both server and
  // client; useLayoutEffect's sessionStorage check then flipped it off
  // after the first paint.)
  const pathname = usePathname();
  const isHomepage = pathname === '/';

  // Start in `hold` so first-time homepage visitors get a plain black frame
  // while the splash images decode. Returning visitors / Clerk redirects must
  // skip *before paint* — `useEffect` runs too late and any rendered mark
  // would flash for a frame after sessionStorage already says the intro ran.
  const [phase, setPhase] = useState<'hold' | 'play' | 'done'>(
    isHomepage ? 'hold' : 'done',
  );

  useLayoutEffect(() => {
    if (typeof window === 'undefined') return;
    if (!isHomepage) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const alreadyPlayed = window.sessionStorage.getItem(SESSION_KEY) === '1';
    if (reduced || alreadyPlayed) setPhase('done');
  }, [isHomepage]);

  useEffect(() => {
    if (phase !== 'hold') return;
    let cancelled = false;
    const ready = Promise.all(SPLASH_ASSETS.map(decodeSplashAsset));
    const cappedReady = Promise.race([ready, wait(MAX_ASSET_WAIT_MS)]);

    Promise.all([cappedReady, wait(READY_HOLD_MS)]).then(() => {
      if (cancelled) return;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (!cancelled) setPhase('play');
        });
      });
    });

    return () => {
      cancelled = true;
    };
  }, [phase]);

  useEffect(() => {
    if (phase !== 'play') return;
    const t = window.setTimeout(() => {
      window.sessionStorage.setItem(SESSION_KEY, '1');
      setPhase('done');
    }, TOTAL_MS);
    return () => window.clearTimeout(t);
  }, [phase]);

  if (phase === 'done') return null;

  return (
    <div
      role="status"
      aria-label="Loading Quantiv"
      className={`quantiv-splash${phase === 'play' ? ' quantiv-splash--play' : ''}`}
    >
      {phase === 'play' && (
        <div className="quantiv-splash-mark" aria-hidden="true">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/QuantivSplashQClosed.png"
            alt=""
            className="quantiv-splash-layer quantiv-splash-closed-ring"
            draggable={false}
          />
          <div className="quantiv-splash-slit-arc" />
          <div className="quantiv-splash-tail-clip">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/brand/QuantivSplashTail.png"
              alt=""
              className="quantiv-splash-layer"
              draggable={false}
            />
          </div>
        </div>
      )}
    </div>
  );
}
