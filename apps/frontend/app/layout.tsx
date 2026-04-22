import './globals.css';
import type { Metadata } from 'next';
import { Providers } from './providers';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Topbar } from '@/components/Topbar';
import { Footer } from '@/components/Footer';

export const metadata: Metadata = {
  title: 'Quantiv — Options Trading Intelligence',
  description: 'Instantly see market-implied moves before earnings. Compute expected moves, greeks, and IV rank from live options chains.',
  keywords: ['options', 'trading', 'earnings', 'implied volatility', 'greeks', 'expected move'],
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
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <ErrorBoundary>
            <div className="min-h-screen flex flex-col">
              <Topbar />
              <main className="flex-1">{children}</main>
              <Footer />
            </div>
          </ErrorBoundary>
        </Providers>
      </body>
    </html>
  );
}
