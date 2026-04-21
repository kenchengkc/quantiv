import './globals.css';
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { Analytics } from '@vercel/analytics/next';
import { Providers } from './providers';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Navbar } from '@/components/Navbar';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Quantiv — Options Trading Intelligence',
  description: 'Instantly see market-implied moves before earnings. Compute expected moves, greeks, and IV rank from live options chains.',
  keywords: ['options', 'trading', 'earnings', 'implied volatility', 'greeks', 'expected move'],
  authors: [{ name: 'Quantiv' }],
  openGraph: {
    title: 'Quantiv — Options Trading Intelligence',
    description: 'Know the move before earnings. Expected moves, Greeks, IV rank from live options chains.',
    type: 'website',
    locale: 'en_US',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Quantiv — Options Trading Intelligence',
    description: 'Know the move before earnings.',
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Providers>
          <ErrorBoundary>
            <div className="min-h-screen flex flex-col bg-[#0b0e14] text-gray-100">
              <Navbar />
              <main className="flex-1">{children}</main>
              <footer className="border-t border-white/5">
                <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-6">
                  <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
                    <div className="flex items-center gap-2">
                      <div className="flex h-6 w-6 items-center justify-center rounded-md bg-emerald-500 text-black font-bold text-xs">
                        Q
                      </div>
                      <span className="text-sm font-semibold text-gray-200">Quantiv</span>
                    </div>
                    <p className="text-xs text-gray-500">
                      &copy; {new Date().getFullYear()} Quantiv · Expected moves from options math + ML
                    </p>
                  </div>
                </div>
              </footer>
            </div>
          </ErrorBoundary>
        </Providers>
        <Analytics />
      </body>
    </html>
  );
}
