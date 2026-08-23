'use client';

import { useEffect, useLayoutEffect, useState } from 'react';
import Image from 'next/image';
import {
  markSplashPlayed,
  shouldSkipSplash,
  SPLASH_SKIP_ATTRIBUTE,
} from '@/lib/splashSession';

// The visual sequence used to begin only after hydration and asset decoding.
// It now starts from server-rendered markup, and is 23% shorter than the old
// 2.327-second sequence without removing any of its visual beats.
const TOTAL_MS = 1_800;

export function Splash() {
  const [done, setDone] = useState(false);

  useLayoutEffect(() => {
    if (shouldSkipSplash()) {
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
      setDone(true);
    }
  }, []);

  useEffect(() => {
    if (done || shouldSkipSplash()) return;

    const navigation = performance.getEntriesByType('navigation')[0] as
      | PerformanceNavigationTiming
      | undefined;
    const elapsedSinceFirstByte = navigation
      ? Math.max(0, performance.now() - navigation.responseStart)
      : 0;
    const remainingMs = Math.max(0, TOTAL_MS - elapsedSinceFirstByte);

    const timeoutId = window.setTimeout(() => {
      markSplashPlayed();
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
      setDone(true);
    }, remainingMs);

    return () => window.clearTimeout(timeoutId);
  }, [done]);

  if (done) return null;

  return (
    <div
      role="status"
      aria-label="Opening Quantiv"
      className="quantiv-splash quantiv-splash--play"
    >
      <div className="quantiv-splash-mark" aria-hidden="true">
        {/* These source WebPs are already compact. `priority` emits resource
            hints from the server HTML; `unoptimized` avoids a cold image-
            transformation request in the high percentile. */}
        <Image
          src="/brand/QuantivSplashQClosed.webp"
          alt=""
          width={4096}
          height={4096}
          priority
          unoptimized
          className="quantiv-splash-layer quantiv-splash-closed-ring"
          draggable={false}
        />
        <div className="quantiv-splash-slit-arc" />
        <div className="quantiv-splash-tail-clip">
          <Image
            src="/brand/QuantivSplashTail.webp"
            alt=""
            width={4096}
            height={4096}
            priority
            unoptimized
            className="quantiv-splash-layer"
            draggable={false}
          />
        </div>
      </div>
      <span className="quantiv-splash-wordmark" aria-hidden="true">
        QUANTIV
      </span>
    </div>
  );
}
