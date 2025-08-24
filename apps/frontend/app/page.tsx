import Link from 'next/link';
import { MagnifyingGlassIcon, ChartBarIcon, ClockIcon } from '@heroicons/react/24/outline';
import SymbolSearch from '@/components/SymbolSearch';
import { WatchlistPanel } from '@/components/WatchlistPanel';

export default function HomePage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* Header */}
      <header className="bg-white shadow-sm">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between">
            <div className="flex items-center">
              <h1 className="text-2xl font-bold text-gray-900">Quantiv</h1>
              <span className="ml-2 rounded-full bg-blue-100 px-2 py-1 text-xs font-medium text-blue-800">
                BETA
              </span>
            </div>
            <nav className="flex space-x-8">
              <Link href="/about" className="text-gray-500 hover:text-gray-900">
                About
              </Link>
            </nav>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <main className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-12">
        <div className="text-center">
          <h1 className="text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">
            Options Trading
            <span className="text-blue-600"> Intelligence</span>
          </h1>
          <p className="mt-6 text-lg leading-8 text-gray-600 max-w-2xl mx-auto">
            Instantly see market-implied moves before earnings. Get expected moves, IV rank, 
            and Greeks from live options chains with precision and speed.
          </p>
          
          {/* Search Bar */}
          <div className="mt-10 flex justify-center">
            <div className="w-full max-w-md">
              <SymbolSearch />
              <p className="mt-2 text-sm text-gray-500 text-center">
                Try: AAPL, TSLA, NVDA, SPY, QQQ
              </p>
            </div>
          </div>
        </div>

        {/* Features */}
        <div className="mt-20">
          <div className="grid grid-cols-1 gap-8 sm:grid-cols-3">
            <div className="text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-blue-500">
                <ChartBarIcon className="h-6 w-6 text-white" aria-hidden="true" />
              </div>
              <h3 className="mt-4 text-lg font-medium text-gray-900">Expected Moves</h3>
              <p className="mt-2 text-sm text-gray-500">
                Straddle and IV-based calculations with 1σ and 2σ price bands
              </p>
            </div>
            
            <div className="text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-green-500">
                <ClockIcon className="h-6 w-6 text-white" aria-hidden="true" />
              </div>
              <h3 className="mt-4 text-lg font-medium text-gray-900">IV Rank</h3>
              <p className="mt-2 text-sm text-gray-500">
                Current implied volatility vs 252-day historical range
              </p>
            </div>
            
            <div className="text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-purple-500">
                <MagnifyingGlassIcon className="h-6 w-6 text-white" aria-hidden="true" />
              </div>
              <h3 className="mt-4 text-lg font-medium text-gray-900">Earnings History</h3>
              <p className="mt-2 text-sm text-gray-500">
                Last 8 realized moves and next earnings date
              </p>
            </div>
          </div>
        </div>

        {/* Platform Status */}
        <div className="mt-20 grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Live Features */}
          <div className="rounded-lg bg-white p-8 shadow-lg">
            <h2 className="text-2xl font-bold text-gray-900 mb-6">
              🚀 Live Platform
            </h2>
            <div className="space-y-4">
              <div className="space-y-2 text-sm">
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-3"></div>
                  <span className="font-medium">Options Chain Analysis</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-3"></div>
                  <span className="font-medium">Expected Move Calculations</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-3"></div>
                  <span className="font-medium">IV Rank & Percentile</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-3"></div>
                  <span className="font-medium">ATM Greeks (Δ, Γ, Θ, ν)</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-3"></div>
                  <span className="font-medium">Earnings History Analysis</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-3"></div>
                  <span className="font-medium">Watchlist & Symbol Search</span>
                </div>
              </div>
              <div className="mt-6 pt-4 border-t border-gray-200">
                <p className="text-sm text-gray-600">
                  All core features are live and ready to use. Start by searching for any ticker symbol above.
                </p>
              </div>
            </div>
          </div>

          {/* Watchlist Panel */}
          <div className="space-y-6">
            <WatchlistPanel />
            <div className="rounded-lg bg-blue-50 p-6">
              <h3 className="text-lg font-semibold text-blue-900 mb-2">
                Quick Start
              </h3>
              <ul className="space-y-2 text-sm text-blue-800">
                <li>• Search for any ticker symbol above</li>
                <li>• View expected moves and options data</li>
                <li>• Click the star to add to watchlist</li>
                <li>• Analyze earnings history and IV rank</li>
              </ul>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
