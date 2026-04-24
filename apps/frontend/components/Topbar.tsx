'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { Search, ChevronRight } from 'lucide-react';
import { SignedIn, SignedOut, UserButton } from '@clerk/nextjs';

const NAV = [
  { href: '/', label: 'Earnings calendar' },
  { href: '/screener', label: 'Screener' },
  { href: '/watchlist', label: 'Watchlist' },
  { href: '/about', label: 'About' },
];

function useClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

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
    <div ref={wrapRef} className="relative">
      <form onSubmit={(e) => { e.preventDefault(); go(query); }} className="relative">
        <Search
          size={13}
          className="absolute left-[10px] top-1/2 -translate-y-1/2"
          style={{ color: 'var(--ink-4)' }}
        />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          placeholder="Jump to ticker"
          className="outline-none transition-colors"
          style={{
            background: 'transparent',
            border: '1px solid var(--line)',
            color: 'var(--ink)',
            padding: '6px 12px 6px 30px',
            borderRadius: 999,
            fontSize: 12,
            width: 180,
            fontFamily: 'inherit',
          }}
          onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--line-2)')}
          onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--line)')}
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
              className="w-full px-4 py-3 text-left flex items-center justify-between transition-colors"
              style={{
                borderBottom: i < suggestions.length - 1 ? '1px solid var(--line)' : 'none',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-3)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <div>
                <span className="serif" style={{ fontWeight: 700, color: 'var(--ink)', fontSize: 13 }}>
                  {item.symbol}
                </span>
                <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--ink-3)' }}>
                  {item.name}
                </span>
              </div>
              <ChevronRight size={14} style={{ color: 'var(--ink-4)' }} />
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
        style={{
          maxWidth: 1240,
          margin: '0 auto',
          padding: '12px 28px',
          display: 'flex',
          alignItems: 'center',
          gap: 18,
        }}
      >
        <Link href="/" aria-label="Quantiv home" className="flex items-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/QuantivColorBanner.png"
            alt="Quantiv"
            style={{ height: 40, width: 'auto', display: 'block' }}
          />
        </Link>

        <nav
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

        <div className="mono tnum" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
          {time} EDT
        </div>
        <NavSearch />
        <span className="kbd">⌘K</span>

        <SignedOut>
          <Link
            href="/sign-in"
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
          <UserButton
            afterSignOutUrl="/"
            appearance={{ elements: { avatarBox: { width: 28, height: 28 } } }}
          />
        </SignedIn>
      </div>
    </header>
  );
}
