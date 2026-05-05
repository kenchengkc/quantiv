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
            {/* Inflections: outer edge has a small dip-then-rise at the
                start of the tail (cubic 3) and a counter-curve flick
                near the tip (cubic 5). Inner edge mirrors with a small
                wave at the base of the tail (cubic 8). The right tip
                is a geometric cusp — cp2 of cubic 5 and cp1 of cubic 6
                share the same coords (246, 80), making the two tangents
                anti-parallel at (248, 70) for a perfectly sharp point. */}
            <path
              d="M 50 124
                 C 56 150, 86 186, 128 194
                 C 168 200, 198 192, 218 174
                 C 222 184, 230 170, 234 162
                 C 246 138, 254 108, 252 86
                 C 252 76, 246 80, 248 70
                 C 246 80, 246 92, 240 102
                 C 230 124, 224 140, 218 146
                 C 216 154, 208 152, 200 154
                 C 180 160, 156 164, 122 162
                 C 88 158, 62 142, 50 124 Z"
              fill="url(#quantivSplashWave)"
            />
          </g>
        </svg>
      </div>
    </div>
  );
}
