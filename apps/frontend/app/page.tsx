'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Search, ChevronRight } from 'lucide-react';
import EarningsGrid from '@/components/EarningsGrid';

function HeroSearch() {
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const router = useRouter();
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    if (!query) {
      setSuggestions([]);
      setIsOpen(false);
      return;
    }
    const controller = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}&limit=6`, {
          signal: controller.signal,
        });
        if (!res.ok) return;
        const json = await res.json();
        const results = json.data ?? [];
        setSuggestions(results);
        setIsOpen(results.length > 0);
      } catch {
        /* ignore */
      }
    }, 200);
    return () => {
      controller.abort();
      clearTimeout(t);
    };
  }, [query]);

  const go = (sym: string) => {
    if (sym) {
      router.push(`/${sym.toUpperCase()}`);
      setQuery('');
      setIsOpen(false);
    }
  };

  return (
    <div ref={wrapperRef} className="relative w-full max-w-xl mx-auto">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          go(query);
        }}
        className="relative group"
      >
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          placeholder="Search any ticker — AAPL, TSLA, NVDA..."
          className="w-full h-12 pl-12 pr-5 rounded-xl bg-gray-900/70 border border-white/10 text-gray-100 text-base placeholder:text-gray-500 focus:outline-none focus:border-emerald-400/50 focus:ring-2 focus:ring-emerald-400/20 transition-all"
        />
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500 group-focus-within:text-emerald-400 transition-colors" />
      </form>

      {isOpen && suggestions.length > 0 && (
        <div className="absolute z-50 w-full mt-2 bg-gray-900 border border-white/10 rounded-xl shadow-2xl overflow-hidden">
          {suggestions.map((item) => (
            <button
              key={item.symbol}
              onClick={() => go(item.symbol)}
              className="w-full px-4 py-3 text-left hover:bg-white/5 transition-colors flex items-center justify-between border-b border-white/5 last:border-0"
            >
              <div>
                <span className="font-semibold text-white">{item.symbol}</span>
                <span className="ml-3 text-sm text-gray-400">{item.name}</span>
              </div>
              <ChevronRight className="h-4 w-4 text-gray-500" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  return (
    <div className="min-h-screen bg-[#0b0e14] text-gray-100">
      <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 pt-10 pb-6">
        <div className="text-center mb-8">
          <h1 className="text-3xl sm:text-5xl font-bold tracking-tight text-white">
            Know the move <span className="text-emerald-400">before earnings.</span>
          </h1>
          <p className="mt-3 text-sm sm:text-base text-gray-400 max-w-2xl mx-auto">
            Click any ticker to see ML-powered expected move, implied volatility, and options
            Greeks — computed from 7 years of options data.
          </p>
        </div>
        <HeroSearch />
      </section>

      <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 pb-16">
        <EarningsGrid />
      </section>
    </div>
  );
}
