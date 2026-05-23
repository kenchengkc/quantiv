'use client';

/* eslint-disable @next/next/no-img-element */

import { type CSSProperties, useEffect, useState } from 'react';

export type TickerLogoLoadState = 'loaded' | 'failed';

const DEFAULT_TIMEOUT_MS = 4_000;
const logoStateCache = new Map<string, TickerLogoLoadState>();
const logoPromiseCache = new Map<string, Promise<TickerLogoLoadState>>();

function normalizeTicker(ticker: string) {
  return ticker.trim().toUpperCase();
}

export function tickerLogoUrl(ticker: string) {
  return `https://assets.parqet.com/logos/symbol/${normalizeTicker(ticker)}?format=png`;
}

export function getTickerLogoState(ticker: string): TickerLogoLoadState | undefined {
  return logoStateCache.get(normalizeTicker(ticker));
}

export function hasTickerLogoState(ticker: string) {
  return logoStateCache.has(normalizeTicker(ticker));
}

export function setTickerLogoState(ticker: string, state: TickerLogoLoadState) {
  logoStateCache.set(normalizeTicker(ticker), state);
}

export function preloadTickerLogo(
  ticker: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<TickerLogoLoadState> {
  const normalized = normalizeTicker(ticker);
  const cached = logoStateCache.get(normalized);
  if (cached) return Promise.resolve(cached);

  const existing = logoPromiseCache.get(normalized);
  if (existing) return existing;

  if (typeof window === 'undefined') return Promise.resolve('failed');

  const promise = new Promise<TickerLogoLoadState>((resolve) => {
    const img = new window.Image();
    let settled = false;
    let timeoutId: number | null = null;

    const finish = (state: TickerLogoLoadState) => {
      if (settled) return;
      settled = true;
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      logoStateCache.set(normalized, state);
      resolve(state);
    };

    timeoutId = window.setTimeout(() => finish('failed'), timeoutMs);
    img.decoding = 'async';
    img.onload = () => {
      const decode =
        typeof img.decode === 'function'
          ? img.decode().catch(() => undefined)
          : Promise.resolve();
      void decode.then(() => finish('loaded'));
    };
    img.onerror = () => finish('failed');
    img.src = tickerLogoUrl(normalized);

    if (img.complete) {
      finish(img.naturalWidth > 0 ? 'loaded' : 'failed');
    }
  });

  logoPromiseCache.set(normalized, promise);
  return promise;
}

export function preloadTickerLogos(
  tickers: string[],
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
) {
  return Promise.all(tickers.map((ticker) => preloadTickerLogo(ticker, timeoutMs)));
}

type TickerLogoProps = {
  ticker: string;
  size?: number;
  radius?: number;
  alt?: string;
  loading?: 'eager' | 'lazy';
  className?: string;
  style?: CSSProperties;
  fallbackClassName?: string;
  fallbackStyle?: CSSProperties;
};

export function TickerLogo({
  ticker,
  size = 24,
  radius = 6,
  alt,
  loading = 'lazy',
  className,
  style,
  fallbackClassName = 'serif',
  fallbackStyle,
}: TickerLogoProps) {
  const normalized = normalizeTicker(ticker);
  const [failed, setFailed] = useState(
    () => getTickerLogoState(normalized) === 'failed',
  );

  useEffect(() => {
    setFailed(getTickerLogoState(normalized) === 'failed');
  }, [normalized]);

  const baseStyle: CSSProperties = {
    width: size,
    height: size,
    flexShrink: 0,
  };
  const accessibleAlt = alt ?? normalized;

  if (failed) {
    return (
      <span
        aria-hidden={accessibleAlt === ''}
        aria-label={accessibleAlt === '' ? undefined : accessibleAlt}
        role={accessibleAlt === '' ? undefined : 'img'}
        className={fallbackClassName}
        style={{
          ...baseStyle,
          borderRadius: radius,
          background: 'var(--bg-3)',
          border: '1px solid var(--line)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--ink-2)',
          fontSize: Math.max(9, size * 0.34),
          fontWeight: 600,
          letterSpacing: '0.02em',
          ...fallbackStyle,
        }}
      >
        {normalized.slice(0, 3)}
      </span>
    );
  }

  return (
    <img
      src={tickerLogoUrl(normalized)}
      alt={accessibleAlt}
      width={size}
      height={size}
      loading={loading}
      decoding="async"
      className={className}
      onLoad={() => {
        setTickerLogoState(normalized, 'loaded');
        setFailed(false);
      }}
      onError={() => {
        setTickerLogoState(normalized, 'failed');
        setFailed(true);
      }}
      style={{
        ...baseStyle,
        borderRadius: radius,
        objectFit: 'cover',
        background: 'var(--paper)',
        border: '1px solid var(--line)',
        display: 'block',
        ...style,
      }}
    />
  );
}
