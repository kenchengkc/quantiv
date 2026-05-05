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

          {/* Volatility smile — authored as a closed, filled silhouette so
              the two ends taper to *sharp points* (matching the brand
              mark) instead of reading as a stroked line with rounded caps.

              Path layout (clockwise from the left tip):

                · LEFT TIP at (50, 124) — pointed, inside the ring's hole
                  at roughly the 7-8 o'clock interior
                · OUTER edge: dives below the ring's bottom (y ≈ 192,
                  ring bottom is at y=170), arcs along the underside of
                  the dip, then sweeps up the right side past the ring
                · RIGHT TIP at (244, 78) — pointed, sitting at the ring's
                  upper-right shoulder (not floating high above it)
                · INNER edge: returns through the upper edge of the
                  rising tail, across the top of the dip, back to the
                  left tip.

              Body width is widest (~34 px) through the dip and the
              transition into the tail, narrows to ~22 px through the
              rising portion, and tapers to 0 at both tips — visually
              consistent with the brand wave's calligraphic feel. */}
          <g className="quantiv-splash-wave">
            <path
              d="M 50 124
                 C 56 148, 84 184, 128 192
                 C 168 198, 196 184, 218 158
                 C 232 142, 245 110, 244 78
                 C 240 92, 226 110, 208 128
                 C 188 148, 156 162, 116 158
                 C 84 154, 60 142, 50 124 Z"
              fill="url(#quantivSplashWave)"
            />
          </g>
        </svg>
      </div>
    </div>
  );
}
