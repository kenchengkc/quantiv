'use client';

import { useEffect, useRef, useState } from 'react';
import { companyName } from '@/lib/companyNames';
import { TickerLogo } from '@/components/TickerLogo';

const TICKER_HOVER_DELAY_MS = 900;

type ShowDetail = { ticker: string; x: number; y: number };

function dispatch<T>(name: string, detail?: T) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(name, detail !== undefined ? { detail } : undefined));
}

export function showTickerHover(ticker: string, x: number, y: number) {
  dispatch<ShowDetail>('qv-hover-show', { ticker, x, y });
}
export function hideTickerHover() {
  dispatch('qv-hover-hide');
}

export function useTickerHover(ticker: string) {
  const timerRef = useRef<number | null>(null);
  const lastPosRef = useRef({ x: 0, y: 0 });
  const shownRef = useRef(false);

  function clearTimer() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }

  function onMouseEnter(e: { clientX: number; clientY: number }) {
    lastPosRef.current = { x: e.clientX, y: e.clientY };
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      shownRef.current = true;
      showTickerHover(ticker, lastPosRef.current.x, lastPosRef.current.y);
    }, TICKER_HOVER_DELAY_MS);
  }
  function onMouseMove(e: { clientX: number; clientY: number }) {
    if (!shownRef.current) lastPosRef.current = { x: e.clientX, y: e.clientY };
  }
  function onMouseLeave() {
    clearTimer();
    if (shownRef.current) {
      shownRef.current = false;
      hideTickerHover();
    }
  }

  useEffect(() => () => {
    clearTimer();
    if (shownRef.current) hideTickerHover();
  }, []);

  return { onMouseEnter, onMouseMove, onMouseLeave };
}

/** Mount once at the App root. Listens for show/hide events and renders a
 *  small floating card pinned to the cursor position at the time it appeared. */
export function TickerHoverHost() {
  const [state, setState] = useState<ShowDetail | null>(null);

  useEffect(() => {
    function onShow(e: Event) {
      const detail = (e as CustomEvent<ShowDetail>).detail;
      setState(detail);
    }
    function onHide() { setState(null); }
    window.addEventListener('qv-hover-show', onShow);
    window.addEventListener('qv-hover-hide', onHide);
    return () => {
      window.removeEventListener('qv-hover-show', onShow);
      window.removeEventListener('qv-hover-hide', onHide);
    };
  }, []);

  if (!state) return null;

  const W = 240;
  const H = 78;
  const margin = 14;
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 720;
  let left = state.x + 16;
  let top = state.y + 18;
  if (left + W + margin > vw) left = state.x - W - 16;
  if (top + H + margin > vh) top = state.y - H - 16;
  if (left < margin) left = margin;
  if (top < margin) top = margin;

  return (
    <div
      role="tooltip"
      aria-label={`${state.ticker} company information`}
      style={{
        position: 'fixed',
        left,
        top,
        width: W,
        zIndex: 9000,
        pointerEvents: 'none',
        background: 'linear-gradient(180deg, var(--bg-3), var(--bg-2))',
        border: '1px solid var(--line-2)',
        borderRadius: 12,
        boxShadow: '0 18px 48px rgba(0,0,0,0.6), 0 0 0 1px color-mix(in oklab, var(--brand-blue-1) 18%, transparent)',
        padding: '12px 14px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        animation: 'qv-hover-pop 180ms cubic-bezier(.2,.8,.3,1) both',
      }}
    >
      <TickerLogo
        ticker={state.ticker}
        size={40}
        radius={6}
        loading="eager"
        fallbackStyle={{
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: 0,
        }}
      />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          className="serif"
          style={{
            fontSize: 16,
            fontWeight: 800,
            color: 'var(--ink)',
            letterSpacing: '-0.01em',
            lineHeight: 1,
            textTransform: 'uppercase',
          }}
        >
          {state.ticker}
        </div>
        <div
          style={{
            fontSize: 11.5,
            color: 'var(--ink-2)',
            marginTop: 4,
            lineHeight: 1.3,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
          }}
        >
          {companyName(state.ticker)}
        </div>
      </div>
    </div>
  );
}
