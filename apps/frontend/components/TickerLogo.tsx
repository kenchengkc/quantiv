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

type LogoSource = 'logodev' | 'finnhub' | 'parqet';

// Default resolution order, highest trust first. Each is tried until one image
// loads; misses cascade to the next (Logo.dev/Finnhub return 404 on unknowns):
//   Logo.dev → Finnhub (cached) → Parqet → (letter tile in render)
// Finnhub stays ahead of Parqet because Parqet keys on bare symbols and can
// return the wrong issuer (e.g. NA → National Bank of Canada vs NASDAQ NA =
// Nano Labs).
const DEFAULT_SOURCE_ORDER: LogoSource[] = ['logodev', 'finnhub', 'parqet'];

// Per-ticker source preference, from manual audit. Moves one provider to the
// front of the chain for tickers where the default primary (Logo.dev) serves
// the wrong brand or has no logo. The other providers stay as fallbacks.
const LOGO_SOURCE_OVERRIDES: Record<string, LogoSource> = {
  // Logo.dev wrong/missing here; Finnhub carries the correct mark.
  DG: 'finnhub',
  URI: 'finnhub',
  PCAR: 'finnhub',
  NTAP: 'finnhub',
  B: 'finnhub',
  ALAB: 'finnhub',
  CMCSA: 'finnhub',
  PAAS: 'finnhub',
  DUK: 'finnhub',
  'BRK.B': 'finnhub',
  LRCX: 'finnhub',
  SMR: 'finnhub',
  MRSH: 'finnhub', // Logo.dev returned no image
  // Logo.dev/Finnhub wrong; Parqet carries the correct mark.
  USAR: 'parqet',
};

// Explicit URL pins for the rare case where *every* provider is wrong. Tried
// before any provider; keep this small.
const LOGO_OVERRIDES: Record<string, string> = {
  // 'NA': 'https://…',  // pin Nano Labs if Logo.dev/Finnhub disagree
};

// Domain pins for tickers no provider resolves by symbol. Logo.dev's domain
// endpoint (img.logo.dev/<domain>) finds these reliably. Use the company's own
// domain — beware brand collisions (P = Everpure Inc., formerly Pure Storage,
// at everpuredata.com — NOT the Pentair water brand at everpure.com).
const LOGO_DOMAIN_OVERRIDES: Record<string, string> = {
  P: 'everpuredata.com',
};

// Publishable (pk_) key, exposed to the client via next.config.js. Safe to embed
// in URLs by design; the secret sk_ key stays server-side only.
const LOGO_DEV_KEY = process.env.NEXT_PUBLIC_LOGO_DEV_PUBLISHABLE_KEY ?? '';

export function tickerLogoUrl(ticker: string) {
  return `https://assets.parqet.com/logos/symbol/${normalizeTicker(ticker)}?format=png`;
}

// Logo.dev resolves by stock ticker and is our highest-quality source. The
// fallback=404 param is critical: without it Logo.dev returns a generated grey
// monogram with HTTP 200 for unknown tickers, which would mask the rest of the
// chain. With it, misses 404 cleanly so the next source gets a turn.
function logoDevUrl(ticker: string): string | null {
  if (!LOGO_DEV_KEY) return null;
  return `https://img.logo.dev/ticker/${normalizeTicker(ticker)}?token=${LOGO_DEV_KEY}&size=200&format=png&fallback=404`;
}

// Logo.dev's domain endpoint, used for LOGO_DOMAIN_OVERRIDES tickers.
function logoDevDomainUrl(ticker: string): string | null {
  const domain = LOGO_DOMAIN_OVERRIDES[normalizeTicker(ticker)];
  if (!domain || !LOGO_DEV_KEY) return null;
  return `https://img.logo.dev/${domain}?token=${LOGO_DEV_KEY}&size=200&format=png&fallback=404`;
}

function cachedFinnhubLogoUrl(ticker: string): string | null {
  const logos = (tickerLogoData as { logos?: Record<string, unknown> }).logos ?? {};
  const url = logos[normalizeTicker(ticker)];
  return typeof url === 'string' && /^https?:\/\//.test(url) ? url : null;
}

// Dual-class tickers carry a separator (BRK.B) that providers key inconsistently:
// Logo.dev and Parqet want the dash form (BF-B), while our Finnhub cache stores
// the dot form (BF.B). Emit both separator variants per provider so whichever
// the provider actually hosts resolves instead of 404-ing the brand.
function separatorVariants(ticker: string): string[] {
  const dot = ticker.replace(/-/g, '.');
  const dash = ticker.replace(/\./g, '-');
  return dot === dash ? [ticker] : [dash, dot];
}

function sourceUrl(source: LogoSource, ticker: string): (string | null)[] {
  switch (source) {
    case 'logodev':
      // Logo.dev hosts dual-class under the dash symbol; try dash first.
      return separatorVariants(ticker).map(logoDevUrl);
    case 'finnhub':
      // Cache is keyed exactly as committed (dot form for BF.B); try both.
      return separatorVariants(ticker).map(cachedFinnhubLogoUrl);
    case 'parqet':
      return separatorVariants(ticker).map(tickerLogoUrl);
  }
}

export function tickerLogoUrls(ticker: string) {
  const normalized = normalizeTicker(ticker);
  // A preferred source (if any) jumps to the front; the rest follow in default
  // order so a wrong/missing image still cascades to a working fallback.
  const preferred = LOGO_SOURCE_OVERRIDES[normalized];
  const order: LogoSource[] = preferred
    ? [preferred, ...DEFAULT_SOURCE_ORDER.filter((s) => s !== preferred)]
    : DEFAULT_SOURCE_ORDER;
  const candidates: (string | null)[] = [
    LOGO_OVERRIDES[normalized] ?? null,
    logoDevDomainUrl(normalized),
    ...order.flatMap((source) => sourceUrl(source, normalized)),
  ];
  const urls: string[] = [];
  for (const url of candidates) {
    if (url && !urls.includes(url)) urls.push(url);
  }
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
    setTickerLogoLoaded(
      normalized,
      tickerLogoUrls(normalized)[0] ?? tickerLogoUrl(normalized),
    );
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
