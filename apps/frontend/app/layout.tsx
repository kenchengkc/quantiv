import './globals.css';
import type { Metadata } from 'next';
import { ClerkProvider } from '@clerk/nextjs';
import { Providers } from './providers';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Topbar } from '@/components/Topbar';
import { Footer } from '@/components/Footer';
import { Analytics } from '@vercel/analytics/next';

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
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: '#8ab4ff',
          colorBackground: '#12151c',
          colorText: '#e6e8ee',
          colorInputBackground: '#0b0e14',
          colorInputText: '#e6e8ee',
          borderRadius: '10px',
        },
      }}
    >
      <html lang="en">
        <body>
          <Providers>
            <ErrorBoundary>
              <div className="min-h-screen flex flex-col">
                <Topbar />
                <main className="flex-1">{children}</main>
                <Footer />
              </div>
              <Analytics />
            </ErrorBoundary>
          </Providers>
        </body>
      </html>
    </ClerkProvider>
  );
}
