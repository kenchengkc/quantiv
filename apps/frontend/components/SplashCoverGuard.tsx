'use client';

import { useLayoutEffect } from 'react';
import {
  shouldSkipSplash,
  SPLASH_FIRST_PAINT_ATTRIBUTE,
  SPLASH_FIRST_PAINT_COVER_ID,
  SPLASH_SKIP_ATTRIBUTE,
} from '@/lib/splashSession';

/**
 * The homepage is `force-dynamic`, so the shared layout hydrates and can
 * paint before `<Splash />` arrives. A blocking head script stamps
 * `data-quantiv-splash-first-paint` to cover that parser gap, but React
 * then reconciles `<html>` against the RSC payload (which does not include
 * that attribute) and strips it — one white/content frame — before the
 * splash overlay mounts. Re-apply the cover in this layout effect, which
 * still runs before the browser paints the layout-only commit.
 */
export function SplashCoverGuard() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (window.location.pathname !== '/') {
      root.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
      root.removeAttribute(SPLASH_FIRST_PAINT_ATTRIBUTE);
      document.getElementById(SPLASH_FIRST_PAINT_COVER_ID)?.remove();
      return;
    }

    if (shouldSkipSplash()) {
      root.setAttribute(SPLASH_SKIP_ATTRIBUTE, '1');
      root.removeAttribute(SPLASH_FIRST_PAINT_ATTRIBUTE);
      document.getElementById(SPLASH_FIRST_PAINT_COVER_ID)?.remove();
      return;
    }

    // Splash is already in this commit — it owns the cover now.
    if (document.querySelector('.quantiv-splash')) return;

    root.removeAttribute(SPLASH_SKIP_ATTRIBUTE);
    root.setAttribute(SPLASH_FIRST_PAINT_ATTRIBUTE, '1');

    // Inline-styled node so the cover does not depend on globals.css having
    // loaded. React does not manage this node; Splash removes it on mount.
    if (!document.getElementById(SPLASH_FIRST_PAINT_COVER_ID)) {
      const cover = document.createElement('div');
      cover.id = SPLASH_FIRST_PAINT_COVER_ID;
      cover.setAttribute('aria-hidden', 'true');
      cover.style.cssText =
        'position:fixed;inset:0;z-index:2147483647;background:#000;pointer-events:auto';
      document.body.appendChild(cover);
    }
  }, []);

  return null;
}
