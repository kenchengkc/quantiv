'use client';

import { useEffect, useLayoutEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import {
  clearSplashGate,
  markSplashPlayed,
  splashAlreadyPlayed,
  SPLASH_SESSION_KEY,
} from '@/lib/splashSession';

// Once-per-session intro. The sequence starts with a closed ring, opens the
// bottom-right slit, then reveals the exact logo tail through it.
const TOTAL_MS = 2327;
const READY_HOLD_MS = 280;
const MAX_ASSET_WAIT_MS = 900;
const SPLASH_ASSETS = [
  '/brand/QuantivSplashQClosed.webp',
  '/brand/QuantivSplashTail.webp',
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

function shouldSkipSplash(): boolean {
  if (typeof window === 'undefined') return true;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  return reduced || splashAlreadyPlayed();
}

export function Splash() {
  // Splash is a homepage-only intro. On any subpage we don't render it at
  // all — including during SSR — so reopening a tab (Cmd+Shift+T) to
  // /screener, /watchlist, /AAPL, etc. doesn't flash the white ring for a
  // frame during hydration.
  const pathname = usePathname();
  const isHomepage = pathname === '/';

  const [phase, setPhase] = useState<'hold' | 'play' | 'done'>(() => {
    if (!isHomepage) return 'done';
    if (typeof window === 'undefined') return 'done';
    return shouldSkipSplash() ? 'done' : 'hold';
  });

  useLayoutEffect(() => {
    if (!isHomepage) {
      clearSplashGate();
      return;
    }
    if (shouldSkipSplash()) {
      clearSplashGate();
      setPhase('done');
    }
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
    if (phase === 'play') clearSplashGate();
  }, [phase]);

  useEffect(() => {
    if (phase !== 'play') return;
    const t = window.setTimeout(() => {
      markSplashPlayed();
      clearSplashGate();
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
            src="/brand/QuantivSplashQClosed.webp"
            alt=""
            className="quantiv-splash-layer quantiv-splash-closed-ring"
            draggable={false}
          />
          <div className="quantiv-splash-slit-arc" />
          <div className="quantiv-splash-tail-clip">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/brand/QuantivSplashTail.webp"
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
