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
        <svg
          viewBox="0 0 220 220"
          width="180"
          height="180"
          aria-hidden="true"
        >
          {/* Q ring — pure white, stroked. Pops in first. */}
          <circle
            className="quantiv-splash-ring"
            cx="110"
            cy="110"
            r="64"
            fill="none"
            stroke="#FAFBFD"
            strokeWidth="20"
            strokeLinecap="round"
          />
          {/* Volatility smile / wave: enters inside the ring at lower-left,
              dips down through the bottom, rises across the right edge of
              the ring, and exits with an upward flourish past the upper-
              right. Single cubic-Bezier path so a stroke-dashoffset draw
              animation reads as one continuous gesture. pathLength=100
              normalizes the dash math regardless of the path's true length. */}
          <path
            className="quantiv-splash-wave"
            d="M 56 118 C 70 162, 118 170, 144 138 C 162 116, 174 84, 204 50"
            pathLength="100"
            fill="none"
            stroke="#1E90FF"
            strokeWidth="16"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </div>
  );
}
