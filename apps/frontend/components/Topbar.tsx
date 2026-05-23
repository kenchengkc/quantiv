'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { Search, ChevronRight, Menu, X } from 'lucide-react';
import { SignedIn, SignedOut, UserButton } from '@clerk/nextjs';

const NAV = [
  { href: '/', label: 'Earnings Calendar' },
  { href: '/screener', label: 'Screener' },
  { href: '/watchlist', label: 'Watchlist' },
  { href: '/about', label: 'About' },
];

function useClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();

    // Align the first interval to the next wall-clock minute boundary so
    // the displayed minute flips ~immediately when the real minute does
    // (instead of lagging up to 59 s based on mount time).
    const msToNextMinute = 60_000 - (Date.now() % 60_000);
    let intervalId: ReturnType<typeof setInterval> | null = null;
    const timeoutId = setTimeout(() => {
      tick();
      intervalId = setInterval(tick, 60_000);
    }, msToNextMinute);

    // Browsers throttle setInterval in backgrounded tabs (some down to 1/min,
    // some pause entirely). Refresh on focus / visibility change so the
    // clock catches up the moment the user looks at it again.
    const onVisible = () => {
      if (document.visibilityState === 'visible') tick();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);

    return () => {
      clearTimeout(timeoutId);
      if (intervalId) clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, []);
  return now;
}

/* eslint-disable @next/next/no-img-element */
/** Tiny ticker logo for the search-suggestions dropdown. HTML width/
 *  height attributes reserve the box before bytes arrive so suggestions
 *  don't reflow as logos stream in. Falls back to a 3-letter monogram. */
function SearchResultLogo({ ticker, size = 24 }: { ticker: string; size?: number }) {
  const [err, setErr] = useState(false);
  if (err) {
    return (
      <span
        aria-hidden
        className="serif"
        style={{
          width: size,
          height: size,
          flexShrink: 0,
          borderRadius: 6,
          background: 'var(--bg-3)',
          border: '1px solid var(--line)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--ink-2)',
          fontSize: Math.max(9, size * 0.34),
          fontWeight: 600,
          letterSpacing: '0.02em',
        }}
      >
        {ticker.slice(0, 3)}
      </span>
    );
  }
  return (
    <img
      src={`https://assets.parqet.com/logos/symbol/${ticker}?format=png`}
      alt=""
      width={size}
      height={size}
      onError={() => setErr(true)}
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: 6,
        objectFit: 'cover',
        background: 'var(--paper)',
        border: '1px solid var(--line)',
        display: 'block',
      }}
    />
  );
}
/* eslint-enable @next/next/no-img-element */

function NavSearch() {
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const router = useRouter();
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!query) { setSuggestions([]); setIsOpen(false); return; }
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}&limit=6`, { signal: ctl.signal });
        if (!res.ok) return;
        const json = await res.json();
        const results = json.data ?? [];
        setSuggestions(results);
        setIsOpen(results.length > 0);
      } catch { /* ignore */ }
    }, 200);
    return () => { ctl.abort(); clearTimeout(t); };
  }, [query]);

  const go = (sym: string) => {
    if (!sym) return;
    router.push(`/${sym.toUpperCase()}`);
    setQuery('');
    setIsOpen(false);
  };

  return (
    <div ref={wrapRef} className="relative" style={{ display: 'flex', alignItems: 'center' }}>
      <form
        onSubmit={(e) => { e.preventDefault(); go(query); }}
        className="relative"
        style={{ display: 'flex', alignItems: 'center', height: 34 }}
      >
        <Search
          size={13}
          className="absolute left-[10px] top-1/2 -translate-y-1/2"
          style={{ color: 'var(--ink-3)' }}
        />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          placeholder="Jump to ticker"
          className="qv-ticker-search-input outline-none transition-colors"
          style={{
            display: 'block',
            height: 34,
            lineHeight: '34px',
            background: 'color-mix(in oklab, var(--bg-2) 88%, transparent)',
            border: '1px solid var(--line-2)',
            boxShadow: 'inset 0 0 0 1px color-mix(in oklab, var(--ink) 4%, transparent)',
            color: 'var(--ink)',
            caretColor: 'var(--ink)',
            padding: '0 12px 0 30px',
            borderRadius: 999,
            fontSize: 12,
            width: 188,
            fontFamily: 'inherit',
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent)';
            e.currentTarget.style.boxShadow = '0 0 0 2px color-mix(in oklab, var(--accent) 18%, transparent)';
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = 'var(--line-2)';
            e.currentTarget.style.boxShadow = 'inset 0 0 0 1px color-mix(in oklab, var(--ink) 4%, transparent)';
          }}
        />
      </form>
      {isOpen && suggestions.length > 0 && (
        <div
          className="absolute right-0 z-50 mt-1.5 overflow-hidden"
          style={{
            width: 288,
            background: 'var(--bg-2)',
            border: '1px solid var(--line)',
            borderRadius: 12,
            boxShadow: '0 20px 60px rgba(0,0,0,.5)',
          }}
        >
          {suggestions.map((item, i) => (
            <button
              key={item.symbol}
              onClick={() => go(item.symbol)}
              className="w-full px-4 py-3 text-left flex items-center transition-colors"
              style={{
                gap: 10,
                borderBottom: i < suggestions.length - 1 ? '1px solid var(--line)' : 'none',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-3)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <SearchResultLogo ticker={item.symbol} />
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  fontSize: 13,
                  color: 'var(--ink-2)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {item.name || item.symbol}
              </span>
              <ChevronRight size={14} style={{ color: 'var(--ink-4)', flexShrink: 0 }} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Topbar() {
  const pathname = usePathname();
  const now = useClock();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close the mobile sheet whenever the route changes so tapping a nav
  // item drops the user on the new page without an open overlay.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Lock background scroll while the mobile menu sheet is open so the
  // page underneath doesn't move when fingers slide on the panel.
  useEffect(() => {
    if (!mobileOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [mobileOpen]);

  const activeHref = (() => {
    const known = new Set(NAV.map((n) => n.href));
    if (pathname && known.has(pathname)) return pathname;
    // Unknown routes (e.g. ticker detail `/AAPL`) fall back to Earnings.
    return '/';
  })();

  const time = now
    ? now.toLocaleString('en-US', {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZone: 'America/New_York',
      })
    : '';

  return (
    <header
      style={{
        borderBottom: '1px solid var(--line)',
        position: 'sticky',
        top: 0,
        zIndex: 40,
        background: 'color-mix(in oklab, var(--bg) 85%, transparent)',
        backdropFilter: 'blur(8px)',
      }}
    >
      <div
        className="qv-m-pad"
        style={{
          maxWidth: 1240,
          margin: '0 auto',
          padding: '12px 28px',
          display: 'flex',
          alignItems: 'center',
          gap: 18,
        }}
      >
        <Link href="/" aria-label="Quantiv home" className="flex items-center qv-topbar-brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/QuantivColorBanner.webp"
            alt="Quantiv"
            style={{ height: 40, width: 'auto', display: 'block' }}
          />
        </Link>

        <nav
          className="qv-m-hide"
          style={{
            display: 'flex',
            gap: 4,
            marginLeft: 16,
            alignSelf: 'stretch',
            alignItems: 'center',
          }}
        >
          {NAV.map((n) => {
            const active = n.href === activeHref;
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-pressed={active}
                style={{
                  position: 'relative',
                  padding: '6px 12px',
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  color: active ? 'var(--ink)' : 'var(--ink-3)',
                  letterSpacing: '0.01em',
                  transition: 'color 140ms ease',
                }}
                onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLElement).style.color = 'var(--ink)'; }}
                onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLElement).style.color = 'var(--ink-3)'; }}
              >
                {n.label}
                {active && (
                  <span
                    style={{
                      position: 'absolute',
                      left: 12,
                      right: 12,
                      bottom: -13,
                      height: 2,
                      background: 'var(--accent)',
                    }}
                  />
                )}
              </Link>
            );
          })}
        </nav>

        <div style={{ flex: 1 }} />

        <div className="mono tnum qv-m-hide" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
          {time} EDT
        </div>
        <NavSearch />
        <span className="kbd qv-m-hide">⌘K</span>

        <SignedOut>
          <Link
            href="/sign-in"
            className="qv-m-hide"
            style={{
              fontSize: 12,
              color: 'var(--ink-2)',
              padding: '6px 14px',
              border: '1px solid var(--line)',
              borderRadius: 999,
              transition: 'border-color 140ms ease, color 140ms ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--line-2)';
              e.currentTarget.style.color = 'var(--ink)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--line)';
              e.currentTarget.style.color = 'var(--ink-2)';
            }}
          >
            Sign in
          </Link>
        </SignedOut>
        <SignedIn>
          <span className="qv-m-hide" style={{ display: 'inline-flex' }}>
            <UserButton
              afterSignOutUrl="/"
              appearance={{ elements: { avatarBox: { width: 28, height: 28 } } }}
            />
          </span>
        </SignedIn>

        {/* Mobile-only hamburger toggle. Hidden on ≥ 641px via .qv-d-hide. */}
        <button
          type="button"
          aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((v) => !v)}
          className="qv-d-hide"
          style={{
            width: 36,
            height: 36,
            display: 'grid',
            placeItems: 'center',
            borderRadius: 8,
            border: '1px solid var(--line)',
            background: 'transparent',
            color: 'var(--ink-2)',
            cursor: 'pointer',
          }}
        >
          {mobileOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
      </div>

      {/* Mobile dropdown sheet. Renders below the header band; tap a nav
          item or outside to dismiss. */}
      {mobileOpen && (
        <div
          className="qv-d-hide"
          style={{
            borderTop: '1px solid var(--line)',
            background: 'var(--bg)',
            padding: '8px 16px 18px',
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
          }}
        >
          {NAV.map((n) => {
            const active = n.href === activeHref;
            return (
              <Link
                key={n.href}
                href={n.href}
                onClick={() => setMobileOpen(false)}
                aria-current={active ? 'page' : undefined}
                style={{
                  padding: '14px 4px',
                  fontSize: 16,
                  fontWeight: active ? 700 : 500,
                  color: active ? 'var(--ink)' : 'var(--ink-2)',
                  letterSpacing: '-0.005em',
                  borderBottom: '1px solid var(--line)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <span>{n.label}</span>
                {active && (
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: 'var(--accent)',
                    }}
                  />
                )}
              </Link>
            );
          })}
          <div
            style={{
              marginTop: 14,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: 12,
            }}
          >
            <span className="mono tnum" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
              {time} EDT
            </span>
            <SignedOut>
              <Link
                href="/sign-in"
                onClick={() => setMobileOpen(false)}
                style={{
                  fontSize: 13,
                  color: 'var(--ink-2)',
                  padding: '8px 16px',
                  border: '1px solid var(--line)',
                  borderRadius: 999,
                }}
              >
                Sign in
              </Link>
            </SignedOut>
            <SignedIn>
              <UserButton
                afterSignOutUrl="/"
                appearance={{ elements: { avatarBox: { width: 32, height: 32 } } }}
              />
            </SignedIn>
          </div>
        </div>
      )}
    </header>
  );
}
