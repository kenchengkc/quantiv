'use client';

import { useEffect, useLayoutEffect, useState } from 'react';

// Once-per-session intro. The sequence starts with a closed ring, opens the
// bottom-right slit, then reveals the exact logo tail through it.
const SESSION_KEY = 'quantiv:splash:played';
const TOTAL_MS = 2327;

export function Splash() {
  // Start in `play` so first-time visitors never see a dashboard flash before
  // the intro (see layout comment below). Returning visitors / Clerk redirects
  // must skip *before paint* — `useEffect` runs too late and the ring flashes
  // for one frame after sessionStorage already says the intro ran.
  const [phase, setPhase] = useState<'play' | 'done'>('play');

  useLayoutEffect(() => {
    if (typeof window === 'undefined') return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const alreadyPlayed = window.sessionStorage.getItem(SESSION_KEY) === '1';
    if (reduced || alreadyPlayed) setPhase('done');
  }, []);

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
    </div>
  );
}
