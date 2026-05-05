'use client';

import { useEffect, useState } from 'react';

// Once-per-session "Batman-style" intro: the Q's ring pops in, the
// volatility smile draws itself through the lower-left of the ring,
// dips down, rises out the right with a flourishing tail, then the
// whole mark zooms forward and dissolves to reveal the dashboard.
const SESSION_KEY = 'quantiv:splash:played';
const TOTAL_MS = 2400;

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

    window.sessionStorage.setItem(SESSION_KEY, '1');
    setPhase('play');
    const t = window.setTimeout(() => setPhase('done'), TOTAL_MS);
    return () => window.clearTimeout(t);
  }, []);

  if (phase !== 'play') return null;

  return (
    <div
      role="status"
      aria-label="Loading Quantiv"
      className="quantiv-splash"
    >
      <div className="quantiv-splash-mark">
        {/* The viewBox is wider than tall so the ring sits left-of-center
            and the wave's flourish has room to extend past it on the right.
            Ring is at (95, 110) with r=60; the wave enters the ring's
            lower-left interior, dips below the ring's bottom edge (y≈170),
            arcs back up around the ring's right side (without re-crossing
            the stroke), then flourishes up and out to the upper right. */}
        <svg
          viewBox="0 0 260 220"
          width="260"
          height="220"
          aria-hidden="true"
        >
          <defs>
            {/* Brand wave gradient: deeper blue at the bottom-left of the
                smile, brightening toward the upper-right tail. */}
            <linearGradient
              id="quantivSplashWave"
              x1="0"
              y1="1"
              x2="1"
              y2="0"
            >
              <stop offset="0%" stopColor="#0F5FD1" />
              <stop offset="55%" stopColor="#1E90FF" />
              <stop offset="100%" stopColor="#3FB6FF" />
            </linearGradient>
          </defs>

          {/* Q ring — pure white, stroked. Pops in first. */}
          <circle
            className="quantiv-splash-ring"
            cx="95"
            cy="110"
            r="60"
            fill="none"
            stroke="#FAFBFD"
            strokeWidth="22"
          />

          {/* Volatility smile. Single continuous path of two cubic Beziers
              so the stroke-dashoffset reveal reads as one hand gesture.
              pathLength="100" normalizes the dash math. */}
          <path
            className="quantiv-splash-wave"
            d="M 50 122 C 64 178, 100 198, 148 180 C 192 164, 218 100, 248 42"
            pathLength="100"
            fill="none"
            stroke="url(#quantivSplashWave)"
            strokeWidth="20"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </div>
  );
}
