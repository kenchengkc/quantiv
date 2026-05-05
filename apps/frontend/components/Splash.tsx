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

          {/* Volatility smile — authored as a filled silhouette so the two
              ends taper to sharp points (matching the brand mark) instead
              of reading as a stroked line with rounded caps.

              The closed path traces:
                · LEFT TIP at (50, 122) — pointed, inside the ring's hole
                · OUTER edge clockwise: down through the bottom of the dip,
                  along the right side of the rising tail, up to the
                · RIGHT TIP at (254, 42) — pointed, past the ring's upper-right
                · INNER edge back: down the left side of the rising tail,
                  across the top of the dip, returning to the left tip.

              Body thickness widens to ~30 px in the middle of the dip and
              tapers to zero at both tips. */}
          <g className="quantiv-splash-wave">
            <path
              d="M 50 122
                 C 60 150, 88 180, 124 184
                 C 156 187, 184 172, 204 146
                 C 228 114, 250 72, 254 42
                 C 245 62, 224 98, 200 124
                 C 176 148, 138 154, 102 150
                 C 78 148, 62 136, 50 122 Z"
              fill="url(#quantivSplashWave)"
            />
          </g>
        </svg>
      </div>
    </div>
  );
}
