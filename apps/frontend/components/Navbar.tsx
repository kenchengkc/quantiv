'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useState, useRef, useEffect } from 'react';
import { Search, X, ChevronRight } from 'lucide-react';

function NavSearch() {
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    if (!query) { setSuggestions([]); setIsOpen(false); return; }
    const controller = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}&limit=6`, { signal: controller.signal });
        if (!res.ok) return;
        const json = await res.json();
        const results = json.data ?? [];
        setSuggestions(results);
        setIsOpen(results.length > 0);
      } catch { /* ignore */ }
    }, 200);
    return () => { controller.abort(); clearTimeout(t); };
  }, [query]);

  const go = (sym: string) => {
    if (sym) { router.push(`/${sym.toUpperCase()}`); setQuery(''); setIsOpen(false); }
  };

  return (
    <div ref={wrapperRef} className="relative">
      <form onSubmit={(e) => { e.preventDefault(); go(query); }} className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          placeholder="Search ticker..."
          className="w-56 h-9 pl-9 pr-8 rounded-lg bg-gray-900 border border-white/10 text-sm text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-emerald-400/50 focus:ring-2 focus:ring-emerald-400/20 transition-all"
        />
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
        {query && (
          <button
            type="button"
            onClick={() => { setQuery(''); inputRef.current?.focus(); }}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </form>

      {isOpen && suggestions.length > 0 && (
        <div className="absolute right-0 z-50 w-72 mt-1.5 bg-gray-900 border border-white/10 rounded-xl shadow-2xl overflow-hidden">
          {suggestions.map((item) => (
            <button
              key={item.symbol}
              onClick={() => go(item.symbol)}
              className="w-full px-4 py-3 text-left hover:bg-white/5 transition-colors flex items-center justify-between border-b border-white/5 last:border-0"
            >
              <div>
                <span className="font-semibold text-sm text-white">{item.symbol}</span>
                <span className="ml-2 text-sm text-gray-400">{item.name}</span>
              </div>
              <ChevronRight className="h-3.5 w-3.5 text-gray-500" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Navbar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-white/5 bg-[#0b0e14]/80 backdrop-blur-lg">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-14 items-center justify-between">
          <div className="flex items-center gap-8">
            <Link href="/" className="flex items-center gap-2.5">
              <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500 text-black font-bold text-sm">
                Q
              </div>
              <span className="text-base font-bold text-gray-100">Quantiv</span>
            </Link>
            <nav className="hidden sm:flex items-center gap-1">
              <Link
                href="/"
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  pathname === '/' ? 'bg-white/5 text-white' : 'text-gray-400 hover:text-white hover:bg-white/5'
                }`}
              >
                Earnings
              </Link>
              <Link
                href="/about"
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  pathname === '/about' ? 'bg-white/5 text-white' : 'text-gray-400 hover:text-white hover:bg-white/5'
                }`}
              >
                About
              </Link>
            </nav>
          </div>
          <NavSearch />
        </div>
      </div>
    </header>
  );
}
