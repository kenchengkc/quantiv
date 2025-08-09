import Link from 'next/link';
import { MagnifyingGlassIcon, ChartBarIcon, ClockIcon } from '@heroicons/react/24/outline';

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
              <div className="relative">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
                  <MagnifyingGlassIcon className="h-5 w-5 text-gray-400" aria-hidden="true" />
                </div>
                <input
                  type="text"
                  placeholder="Enter ticker symbol (e.g., AAPL)"
                  className="block w-full rounded-md border-0 py-3 pl-10 pr-3 text-gray-900 ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-blue-600 sm:text-sm sm:leading-6"
                />
              </div>
              <p className="mt-2 text-sm text-gray-500">
                Try: AAPL, TSLA, NVDA, SPY
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

        {/* Demo Section */}
        <div className="mt-20 rounded-lg bg-white p-8 shadow-lg">
          <h2 className="text-2xl font-bold text-gray-900 text-center mb-8">
            Coming Soon
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">✨ Features</h3>
              <ul className="space-y-2 text-sm text-gray-600">
                <li>• Real-time options chain data</li>
                <li>• Expected move calculations</li>
                <li>• IV rank and percentile</li>
                <li>• ATM Greeks (Δ, Γ, Θ, ν)</li>
                <li>• Earnings history analysis</li>
                <li>• Watchlist and alerts</li>
              </ul>
            </div>
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">🚀 Status</h3>
              <div className="space-y-2 text-sm">
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-green-500 rounded-full mr-2"></div>
                  <span>Black-Scholes Engine: Complete</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-yellow-500 rounded-full mr-2"></div>
                  <span>API Routes: In Progress</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-gray-300 rounded-full mr-2"></div>
                  <span>Data Providers: Pending</span>
                </div>
                <div className="flex items-center">
                  <div className="h-2 w-2 bg-gray-300 rounded-full mr-2"></div>
                  <span>UI Components: Pending</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
