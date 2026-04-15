'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Search, TrendingUp, BarChart3, Zap, ArrowRight, ChevronRight } from 'lucide-react';

const POPULAR_SYMBOLS = [
  { symbol: 'AAPL', name: 'Apple' },
  { symbol: 'TSLA', name: 'Tesla' },
  { symbol: 'NVDA', name: 'NVIDIA' },
  { symbol: 'AMZN', name: 'Amazon' },
  { symbol: 'MSFT', name: 'Microsoft' },
  { symbol: 'META', name: 'Meta' },
  { symbol: 'GOOGL', name: 'Alphabet' },
  { symbol: 'JPM', name: 'JPMorgan' },
];

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
    <div ref={wrapperRef} className="relative w-full max-w-xl mx-auto">
      <form onSubmit={(e) => { e.preventDefault(); go(query); }} className="relative group">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          placeholder="Search any ticker — AAPL, TSLA, NVDA..."
          className="w-full h-14 pl-14 pr-6 rounded-2xl bg-white border-2 border-gray-200 text-gray-900 text-lg placeholder:text-gray-400 focus:outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-500/10 shadow-lg shadow-gray-200/50 transition-all"
        />
        <Search className="absolute left-5 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400 group-focus-within:text-blue-500 transition-colors" />
      </form>

      {isOpen && suggestions.length > 0 && (
        <div className="absolute z-50 w-full mt-2 bg-white border border-gray-200 rounded-xl shadow-2xl overflow-hidden">
          {suggestions.map((item) => (
            <button
              key={item.symbol}
              onClick={() => go(item.symbol)}
              className="w-full px-5 py-3.5 text-left hover:bg-gray-50 transition-colors flex items-center justify-between border-b border-gray-100 last:border-0"
            >
              <div>
                <span className="font-semibold text-gray-900">{item.symbol}</span>
                <span className="ml-3 text-sm text-gray-500">{item.name}</span>
              </div>
              <ChevronRight className="h-4 w-4 text-gray-300" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const FEATURES = [
  {
    icon: TrendingUp,
    title: 'Expected Move',
    description: 'See market-implied price ranges before earnings using ATM straddle pricing and IV event decomposition.',
  },
  {
    icon: BarChart3,
    title: 'IV Intelligence',
    description: 'Track implied volatility rank, percentile, and term structure to know when options are cheap or expensive.',
  },
  {
    icon: Zap,
    title: 'ML Forecasts',
    description: 'Quantile regression models trained on 7 years of options data predict P10/P50/P90 earnings moves.',
  },
];

export default function Home() {
  const router = useRouter();

  return (
    <div className="min-h-screen bg-white">
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-blue-50/80 via-white to-white" />
        <div className="relative mx-auto max-w-5xl px-6 pt-20 pb-16 sm:pt-28 sm:pb-24 text-center">
          <div className="inline-flex items-center gap-2 rounded-full bg-blue-50 border border-blue-200 px-4 py-1.5 mb-8">
            <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
            <span className="text-sm font-medium text-blue-700">Live options data powered by Polygon.io</span>
          </div>

          <h1 className="text-5xl sm:text-7xl font-bold tracking-tight text-gray-900 leading-[1.1]">
            Know the move<br />
            <span className="text-blue-600">before earnings.</span>
          </h1>

          <p className="mt-6 text-lg sm:text-xl text-gray-500 max-w-2xl mx-auto leading-relaxed">
            Quantiv computes expected moves, Greeks, and IV rank from live options chains&mdash;so you can size positions with precision, not guesswork.
          </p>

          <div className="mt-10">
            <HeroSearch />
          </div>

          {/* Popular tickers */}
          <div className="mt-8 flex flex-wrap justify-center gap-2">
            <span className="text-sm text-gray-400 mr-1 self-center">Popular:</span>
            {POPULAR_SYMBOLS.map(({ symbol }) => (
              <button
                key={symbol}
                onClick={() => router.push(`/${symbol}`)}
                className="px-3.5 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm font-medium text-gray-700 transition-colors"
              >
                {symbol}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-5xl px-6 py-20">
        <div className="text-center mb-14">
          <h2 className="text-3xl sm:text-4xl font-bold text-gray-900">
            Professional-grade options intelligence
          </h2>
          <p className="mt-4 text-lg text-gray-500 max-w-2xl mx-auto">
            Built on real options theory and 7 years of historical data, not simplified calculators.
          </p>
        </div>

        <div className="grid gap-8 sm:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, description }) => (
            <div
              key={title}
              className="group rounded-2xl border border-gray-200 bg-white p-8 hover:border-blue-200 hover:shadow-lg hover:shadow-blue-100/50 transition-all"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-blue-50 text-blue-600 group-hover:bg-blue-100 transition-colors">
                <Icon className="h-6 w-6" />
              </div>
              <h3 className="mt-5 text-lg font-semibold text-gray-900">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-gray-500">{description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-5xl px-6 pb-24">
        <div className="rounded-3xl bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900 p-12 sm:p-16 text-center">
          <h2 className="text-3xl sm:text-4xl font-bold text-white">
            Start analyzing earnings moves
          </h2>
          <p className="mt-4 text-lg text-gray-400 max-w-lg mx-auto">
            Search any S&amp;P 500 ticker to see expected moves, IV analysis, and ML-enhanced forecasts instantly.
          </p>
          <button
            onClick={() => router.push('/AAPL')}
            className="mt-8 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-8 py-4 text-lg font-semibold text-white hover:bg-blue-700 transition-colors shadow-lg shadow-blue-600/25"
          >
            Try AAPL
            <ArrowRight className="h-5 w-5" />
          </button>
        </div>
      </section>
    </div>
  );
}
