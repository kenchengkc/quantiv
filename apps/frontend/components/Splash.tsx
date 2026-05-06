'use client';

import { useEffect, useState } from 'react';

// Once-per-session intro. The open Q and volatility-smile tail are rendered
// from transparent layers extracted from the real Quantiv icon. The sequence
// starts with a closed ring, opens the bottom-right slit, then reveals the
// exact logo tail through it.
const SESSION_KEY = 'quantiv:splash:played';
const TOTAL_MS = 3300;

export function Splash() {
  // null on first render (SSR + hydration) so we never produce a markup
  // mismatch. The effect decides whether to play and flips state to true.
  const [phase, setPhase] = useState<'idle' | 'play' | 'done'>('idle');

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const alreadyPlayed =
      window.sessionStorage.getItem(SESSION_KEY) === '1';

    if (reduced || alreadyPlayed) {
      setPhase('done');
      return;
    }

    setPhase('play');
    const t = window.setTimeout(() => {
      window.sessionStorage.setItem(SESSION_KEY, '1');
      setPhase('done');
    }, TOTAL_MS);
    return () => window.clearTimeout(t);
  }, []);

  if (phase !== 'play') return null;

  return (
    <div
      role="status"
      aria-label="Loading Quantiv"
      className="quantiv-splash"
    >
      <div className="quantiv-splash-mark" aria-hidden="true">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/brand/QuantivSplashQClosed.png"
          alt=""
          className="quantiv-splash-layer quantiv-splash-closed-ring"
          draggable={false}
        />
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/brand/QuantivSplashQ.png"
          alt=""
          className="quantiv-splash-layer quantiv-splash-open-q"
          draggable={false}
        />
        <div className="quantiv-splash-slit-clip">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/QuantivSplashSlit.png"
            alt=""
            className="quantiv-splash-layer"
            draggable={false}
          />
        </div>
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
    </div>
  );
}
