import Link from 'next/link';

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between">
            <Link href="/" className="flex items-center">
              <h1 className="text-2xl font-bold text-gray-900">Quantiv</h1>
              <span className="ml-2 rounded-full bg-blue-100 px-2 py-1 text-xs font-medium text-blue-800">
                BETA
              </span>
            </Link>
            <nav className="flex space-x-8">
              <Link href="/" className="text-gray-500 hover:text-gray-900">
                Home
              </Link>
            </nav>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 sm:px-6 lg:px-8 py-12">
        <div className="prose prose-lg mx-auto">
          <h1>About Quantiv</h1>
          
          <p>
            Quantiv is a modern web platform designed for retail options traders and market students 
            who need fast, reliable access to options market intelligence before earnings announcements.
          </p>

          <h2>What We Do</h2>
          <p>
            We compute expected moves, Greeks, and IV rank from live options chains, providing traders 
            with the insights they need to make informed decisions with precision and speed.
          </p>

          <h3>Key Features</h3>
          <ul>
            <li><strong>Expected Move Calculations</strong> - Both straddle and IV-based methods</li>
            <li><strong>IV Rank & Percentile</strong> - Current volatility vs 252-day historical range</li>
            <li><strong>Earnings Analysis</strong> - Next earnings date and last 8 realized moves</li>
            <li><strong>Mini Chain View</strong> - ATM ±3 strikes with mid, IV, and Greeks</li>
            <li><strong>Watchlist</strong> - Track your favorite symbols</li>
          </ul>

          <h3>Methodology</h3>
          <p>
            Our calculations are based on the Black-Scholes model with proper dividend adjustments. 
            We use Brent's method for implied volatility calculations with high precision (1e-6 tolerance).
          </p>

          <h4>Expected Move Formulas</h4>
          <ul>
            <li><strong>Straddle Method:</strong> EM$ ≈ mid(call_ATM + put_ATM)</li>
            <li><strong>IV Method:</strong> EM$ ≈ S₀ · IV_ATM · √T</li>
          </ul>

          <h4>IV Rank Calculation</h4>
          <ul>
            <li><strong>Rank:</strong> (IV_today - IV_min) / (IV_max - IV_min)</li>
            <li><strong>Percentile:</strong> Share of days with IV ≤ today</li>
          </ul>

          <h3>Disclaimer</h3>
          <p className="text-sm text-gray-600 bg-gray-100 p-4 rounded-lg">
            <strong>Important:</strong> This platform is for educational and informational purposes only. 
            Options trading involves substantial risk and is not suitable for all investors. Past performance 
            does not guarantee future results. Please consult with a financial advisor before making any 
            investment decisions.
          </p>

          <h3>Technical Stack</h3>
          <p>
            Built with Next.js, TypeScript, and Tailwind CSS. Powered by real-time options data 
            with Redis caching for optimal performance.
          </p>
        </div>
      </main>
    </div>
  );
}
