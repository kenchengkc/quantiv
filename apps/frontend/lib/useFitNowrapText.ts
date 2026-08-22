'use client';

import { useLayoutEffect, useRef } from 'react';

/** Shrink a nowrap heading to its parent's width; leave maxPx when it already fits. */
export function useFitNowrapText<T extends HTMLElement = HTMLElement>(
  text: string,
  maxPx: number,
  minPx: number,
) {
  const ref = useRef<T | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const fit = () => {
      if (window.matchMedia('(max-width: 640px)').matches) {
        el.style.fontSize = '';
        return;
      }
      el.style.fontSize = `${maxPx}px`;
      const available = el.parentElement?.clientWidth ?? 0;
      if (available <= 0 || el.scrollWidth <= available) return;
      const next = Math.max(minPx, maxPx * (available / el.scrollWidth));
      el.style.fontSize = `${next}px`;
    };

    fit();
    const parent = el.parentElement;
    if (!parent || typeof ResizeObserver === 'undefined') {
      return;
    }
    const observer = new ResizeObserver(fit);
    observer.observe(parent);
    return () => observer.disconnect();
  }, [text, maxPx, minPx]);

  return ref;
}
