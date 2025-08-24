'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Search, X } from 'lucide-react';
import { searchStocks, getPopularStocks, type Stock } from '@/lib/data/stocks';

export default function SymbolSearch() {
  const [query, setQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<Stock[]>([]);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (query.length > 0) {
      const results = searchStocks(query, 8);
      setSuggestions(results);
      setIsOpen(results.length > 0);
    } else {
      // Show popular stocks when no query
      const popular = getPopularStocks();
      setSuggestions(popular);
      setIsOpen(false); // Don't show dropdown for empty query
    }
  }, [query]);

  const handleSubmit = (symbol: string) => {
    if (symbol) {
      router.push(`/${symbol.toUpperCase()}`);
      setQuery('');
      setIsOpen(false);
    }
  };

  return (
    <div ref={wrapperRef} className="relative w-full max-w-md mx-auto">
      <form 
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit(query);
        }}
        className="relative"
      >
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          placeholder="Search symbol..."
          className="w-full pl-10 pr-10 py-2 bg-gray-50 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:bg-white"
        />
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        {query && (
          <button
            type="button"
            onClick={() => {
              setQuery('');
              inputRef.current?.focus();
            }}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </form>

      {isOpen && suggestions.length > 0 && (
        <div className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden max-h-80 overflow-y-auto">
          {suggestions.map(item => (
            <button
              key={item.symbol}
              onClick={() => handleSubmit(item.symbol)}
              className="w-full px-4 py-3 text-left hover:bg-gray-50 focus:bg-gray-50 focus:outline-none border-b border-gray-100 last:border-b-0"
            >
              <div className="flex items-center justify-between">
                <div className="flex flex-col min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-gray-900">{item.symbol}</span>
                    {item.exchange && (
                      <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">
                        {item.exchange}
                      </span>
                    )}
                  </div>
                  <span className="text-sm text-gray-600 truncate">{item.name}</span>
                  {item.sector && (
                    <span className="text-xs text-gray-500">{item.sector}</span>
                  )}
                </div>
                <div className="text-xs text-gray-400 ml-2">
                  →
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
