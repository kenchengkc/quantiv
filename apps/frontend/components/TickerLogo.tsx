'use client';

/* eslint-disable @next/next/no-img-element */

import { type CSSProperties, useEffect, useMemo, useState } from 'react';
import tickerLogoData from '@/public/ticker-logos.json';

export type TickerLogoLoadState = 'loaded' | 'failed';

const DEFAULT_TIMEOUT_MS = 4_000;
const logoStateCache = new Map<string, TickerLogoLoadState>();
const logoUrlCache = new Map<string, string>();
const logoPromiseCache = new Map<string, Promise<TickerLogoLoadState>>();

function normalizeTicker(ticker: string) {
  return ticker.trim().toUpperCase();
}

export function tickerLogoUrl(ticker: string) {
  return `https://assets.parqet.com/logos/symbol/${normalizeTicker(ticker)}?format=png`;
}

function cachedFinnhubLogoUrl(ticker: string): string | null {
  const logos = (tickerLogoData as { logos?: Record<string, unknown> }).logos ?? {};
  const url = logos[normalizeTicker(ticker)];
  return typeof url === 'string' && /^https?:\/\//.test(url) ? url : null;
}

export function tickerLogoUrls(ticker: string) {
  const normalized = normalizeTicker(ticker);
  const urls = [tickerLogoUrl(normalized)];
  const finnhubUrl = cachedFinnhubLogoUrl(normalized);
  if (finnhubUrl && !urls.includes(finnhubUrl)) urls.push(finnhubUrl);
  return urls;
}

export function getTickerLogoState(ticker: string): TickerLogoLoadState | undefined {
  return logoStateCache.get(normalizeTicker(ticker));
}

export function hasTickerLogoState(ticker: string) {
  return logoStateCache.has(normalizeTicker(ticker));
}

export function setTickerLogoState(ticker: string, state: TickerLogoLoadState) {
  const normalized = normalizeTicker(ticker);
  if (state === 'loaded') {
    setTickerLogoLoaded(normalized, tickerLogoUrl(normalized));
  } else {
    setTickerLogoFailed(normalized);
  }
}

function setTickerLogoLoaded(ticker: string, url: string) {
  const normalized = normalizeTicker(ticker);
  logoStateCache.set(normalized, 'loaded');
  logoUrlCache.set(normalized, url);
}

function setTickerLogoFailed(ticker: string) {
  const normalized = normalizeTicker(ticker);
  logoStateCache.set(normalized, 'failed');
  logoUrlCache.delete(normalized);
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

  const urls = tickerLogoUrls(normalized);
  const promise = new Promise<TickerLogoLoadState>((resolve) => {
    let settled = false;
    let timeoutId: number | null = null;
    let index = 0;
    const finish = (state: TickerLogoLoadState) => {
      if (settled) return;
      settled = true;
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      if (state === 'failed') setTickerLogoFailed(normalized);
      resolve(state);
    };

    const tryNext = () => {
      if (settled) return;
      const url = urls[index];
      if (!url) {
        finish('failed');
        return;
      }
      const img = new window.Image();
      img.decoding = 'async';
      img.onload = () => {
        const loadedUrl = url;
        const decode =
          typeof img.decode === 'function'
            ? img.decode().catch(() => undefined)
            : Promise.resolve();
        void decode.then(() => {
          if (settled) return;
          setTickerLogoLoaded(normalized, loadedUrl);
          finish('loaded');
        });
      };
      img.onerror = () => {
        index += 1;
        tryNext();
      };
      img.src = url;

      if (img.complete) {
        if (img.naturalWidth > 0) {
          setTickerLogoLoaded(normalized, url);
          finish('loaded');
        } else {
          index += 1;
          tryNext();
        }
      }
    };

    timeoutId = window.setTimeout(() => finish('failed'), timeoutMs);
    tryNext();
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
  const urls = useMemo(() => tickerLogoUrls(normalized), [normalized]);
  const [failed, setFailed] = useState(
    () => getTickerLogoState(normalized) === 'failed',
  );
  const [srcIndex, setSrcIndex] = useState(() => {
    const loadedUrl = logoUrlCache.get(normalized);
    return loadedUrl ? Math.max(0, urls.indexOf(loadedUrl)) : 0;
  });

  useEffect(() => {
    setFailed(getTickerLogoState(normalized) === 'failed');
    const loadedUrl = logoUrlCache.get(normalized);
    setSrcIndex(loadedUrl ? Math.max(0, urls.indexOf(loadedUrl)) : 0);
  }, [normalized, urls]);

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
      src={urls[srcIndex] ?? tickerLogoUrl(normalized)}
      alt={accessibleAlt}
      width={size}
      height={size}
      loading={loading}
      decoding="async"
      className={className}
      onLoad={() => {
        setTickerLogoLoaded(normalized, urls[srcIndex] ?? tickerLogoUrl(normalized));
        setFailed(false);
      }}
      onError={() => {
        const nextIndex = srcIndex + 1;
        if (nextIndex < urls.length) {
          setSrcIndex(nextIndex);
          return;
        }
        setTickerLogoFailed(normalized);
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
