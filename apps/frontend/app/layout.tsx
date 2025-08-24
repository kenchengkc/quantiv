import './globals.css';
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { Providers } from './providers';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Quantiv - Options Trading Intelligence',
  description: 'Instantly see market-implied moves before earnings. Compute expected moves, greeks, and IV rank from live options chains.',
  keywords: ['options', 'trading', 'earnings', 'implied volatility', 'greeks', 'expected move'],
  authors: [{ name: 'Quantiv' }],
  openGraph: {
    title: 'Quantiv - Options Trading Intelligence',
    description: 'Instantly see market-implied moves before earnings',
    type: 'website',
    locale: 'en_US',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Quantiv - Options Trading Intelligence',
    description: 'Instantly see market-implied moves before earnings',
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
          <div className="min-h-screen bg-gray-50">
            <main>{children}</main>
            <footer className="border-t bg-white py-8">
              <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
                <div className="flex items-center justify-between">
                  <p className="text-sm text-gray-500">
                    © 2024 Quantiv. Built for traders, by traders.
                  </p>
                  <div className="text-sm text-gray-500">
                    People checked moves today: <span id="visitor-count" className="font-semibold">-</span>
                  </div>
                </div>
              </div>
            </footer>
          </div>
        </Providers>
      </body>
    </html>
  );
}
