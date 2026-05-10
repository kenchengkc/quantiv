'use client';

import { useEffect, useState } from 'react';

// Once-per-session intro. The sequence starts with a closed ring, opens the
// bottom-right slit, then reveals the exact logo tail through it.
const SESSION_KEY = 'quantiv:splash:played';
const TOTAL_MS = 2327;

export function Splash() {
  // Render the splash on the first server/client frame. If we wait for
  // useEffect to decide, the dashboard can flash before the intro mounts.
  const [phase, setPhase] = useState<'play' | 'done'>('play');

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const alreadyPlayed =
      window.sessionStorage.getItem(SESSION_KEY) === '1';

    if (reduced || alreadyPlayed) {
      setPhase('done');
      return;
    }

    const t = window.setTimeout(() => {
      window.sessionStorage.setItem(SESSION_KEY, '1');
      setPhase('done');
    }, TOTAL_MS);
    return () => window.clearTimeout(t);
  }, []);

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
