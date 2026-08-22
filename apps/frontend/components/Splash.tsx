'use client';

import { useEffect, useLayoutEffect, useState } from 'react';
import {
  markSplashPlayed,
  removeSplashFirstPaintCover,
  shouldSkipSplash,
  SPLASH_SKIP_ATTRIBUTE,
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

export function Splash() {
  // Mounted only from the homepage. The initial `hold` state is intentional:
  // it lets SSR send the opaque cover. The head guard in app/layout.tsx covers
  // the parser gap before this node arrives and pre-hides returning sessions.
  const [phase, setPhase] = useState<'hold' | 'play' | 'done'>('hold');

  useLayoutEffect(() => {
    // Real splash is in the tree now. Drop the layout/parser cover so the
    // intro can play instead of sitting under a second opaque layer.
    removeSplashFirstPaintCover();

    if (shouldSkipSplash()) {
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
      setPhase('done');
    }
  }, []);

  useEffect(() => {
    if (phase !== 'hold') return;
    if (shouldSkipSplash()) {
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
      setPhase('done');
      return;
    }

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
      markSplashPlayed();
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
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
