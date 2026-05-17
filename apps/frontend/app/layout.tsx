import './globals.css';
import type { Metadata } from 'next';
import { ClerkProvider } from '@clerk/nextjs';
import { Providers } from './providers';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Topbar } from '@/components/Topbar';
import { Footer } from '@/components/Footer';
import { Splash } from '@/components/Splash';
import { Analytics } from '@vercel/analytics/next';
import { SpeedInsights } from '@vercel/speed-insights/next';

export const metadata: Metadata = {
  title: {
    default: 'Quantiv',
    template: '%s | Quantiv',
  },
  description: 'Instantly see market-implied moves before earnings. Compute expected moves, greeks, and IV rank from live options chains.',
  keywords: ['options', 'trading', 'earnings', 'implied volatility', 'greeks', 'expected move'],
  openGraph: {
    title: 'Quantiv',
    description: 'Know the move before earnings. Expected moves, Greeks, IV rank from live options chains.',
    type: 'website',
    locale: 'en_US',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Quantiv',
    description: 'Know the move before earnings.',
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider
      // Clerk's theme engine doesn't understand CSS custom-property references,
      // so we hard-code these to match the global Quantiv palette.
      appearance={{
        variables: {
          colorPrimary: '#1E90FF',          // matches --accent (logo wave blue)
          colorBackground: '#000000',       // pure black page background
          colorText: '#fafbfd',             // near-pure white, matches --ink
          colorInputBackground: '#000000',  // matches --bg
          colorInputText: '#fafbfd',
          borderRadius: '10px',
        },
      }}
    >
      <html lang="en">
        <body>
          <Providers>
            <ErrorBoundary>
              <Splash />
              <div className="min-h-screen flex flex-col">
                <Topbar />
                <main className="flex-1">{children}</main>
                <Footer />
              </div>
              <Analytics />
              <SpeedInsights />
            </ErrorBoundary>
          </Providers>
        </body>
      </html>
    </ClerkProvider>
  );
}
