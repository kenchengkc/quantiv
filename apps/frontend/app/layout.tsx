import './globals.css';
import type { Metadata } from 'next';
import { ClerkProvider } from '@clerk/nextjs';
import { Providers } from './providers';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Topbar } from '@/components/Topbar';
import { Footer } from '@/components/Footer';
import { Splash } from '@/components/Splash';
import { TickerHoverHost } from '@/components/TickerHoverCard';
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

// Explicit mobile viewport — Next.js 15 expects this in its own export so
// the meta tag isn't mistakenly cached as static metadata across themes.
export const viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
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
        <head>
          {/* Load fonts via <link> rather than @import inside globals.css —
              Tailwind's compiled output places its preflight rules before
              the @import, which makes browsers silently ignore the @import
              (per CSS spec, @import must precede every other rule). The
              symptom: Mulish was never actually loading, so the SCREENER h1
              fell back to Nunito Sans and rendered ~5% wider than the design. */}
          <link rel="preconnect" href="https://fonts.googleapis.com" />
          <link
            rel="preconnect"
            href="https://fonts.gstatic.com"
            crossOrigin="anonymous"
          />
          <link
            rel="stylesheet"
            href="https://fonts.googleapis.com/css2?family=Mulish:wght@300;400;500;600;700;800;900&family=Nunito+Sans:opsz,wght@6..12,300;6..12,400;6..12,500;6..12,600&family=JetBrains+Mono:wght@300;400;500;600&display=swap"
          />
        </head>
        <body>
          <Providers>
            <ErrorBoundary>
              <Splash />
              <div className="min-h-screen flex flex-col">
                <Topbar />
                <main className="flex-1">{children}</main>
                <Footer />
              </div>
              <TickerHoverHost />
              <Analytics />
              <SpeedInsights />
            </ErrorBoundary>
          </Providers>
        </body>
      </html>
    </ClerkProvider>
  );
}
