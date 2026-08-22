import './globals.css';
import type { Metadata } from 'next';
import { JetBrains_Mono, Mulish, Nunito_Sans } from 'next/font/google';
import { ClerkProvider } from '@clerk/nextjs';
import { Providers } from './providers';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Topbar } from '@/components/Topbar';
import { Footer } from '@/components/Footer';
import { TickerHoverHost } from '@/components/TickerHoverCard';
import {
  SPLASH_FIRST_PAINT_ATTRIBUTE,
  SPLASH_SESSION_KEY,
  SPLASH_SKIP_ATTRIBUTE,
} from '@/lib/splashSession';
import { SplashCoverGuard } from '@/components/SplashCoverGuard';
import { Analytics } from '@vercel/analytics/next';
import { SpeedInsights } from '@vercel/speed-insights/next';

// Self-host the three typefaces through next/font/google. This sidesteps
// the @import ordering bug we used to work around with a <link> in <head>:
// next/font writes its own preload + font-face rules into Next's <head>
// stream, so there's no @import for Tailwind to reorder past, and the
// browser never sees a third-party fonts.googleapis.com request. Each
// family exposes a CSS variable that globals.css consumes by name.
const mulish = Mulish({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600', '700', '800', '900'],
  variable: '--font-mulish',
  display: 'swap',
});

const nunitoSans = Nunito_Sans({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600'],
  variable: '--font-nunito-sans',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
});

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

function SplashFirstPaintGuard() {
  const script = `
    (() => {
      if (window.location.pathname !== '/') return;

      const root = document.documentElement;
      try {
        const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        const played = window.sessionStorage.getItem(${JSON.stringify(SPLASH_SESSION_KEY)}) === '1';
        if (reduced || played) {
          root.setAttribute(${JSON.stringify(SPLASH_SKIP_ATTRIBUTE)}, '1');
          root.removeAttribute(${JSON.stringify(SPLASH_FIRST_PAINT_ATTRIBUTE)});
        } else {
          root.removeAttribute(${JSON.stringify(SPLASH_SKIP_ATTRIBUTE)});
          root.setAttribute(${JSON.stringify(SPLASH_FIRST_PAINT_ATTRIBUTE)}, '1');
        }
      } catch {
        root.setAttribute(${JSON.stringify(SPLASH_SKIP_ATTRIBUTE)}, '1');
        root.removeAttribute(${JSON.stringify(SPLASH_FIRST_PAINT_ATTRIBUTE)});
      }
    })();
  `;

  // This must execute in <head>, before the browser can parse and paint the
  // shared layout's topbar. The real splash remains homepage-only.
  return <script id="quantiv-splash-first-paint" dangerouslySetInnerHTML={{ __html: script }} />;
}

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
      <html
        lang="en"
        suppressHydrationWarning
        className={`${mulish.variable} ${nunitoSans.variable} ${jetbrainsMono.variable}`}
        style={{ backgroundColor: '#000000', colorScheme: 'dark' }}
      >
        <head>
          <style
            dangerouslySetInnerHTML={{
              __html: 'html,body{background:#000;color-scheme:dark}',
            }}
          />
          <SplashFirstPaintGuard />
        </head>
        <body suppressHydrationWarning style={{ backgroundColor: '#000000' }}>
          <SplashCoverGuard />
          <Providers>
            <ErrorBoundary>
              <div className="min-h-screen flex flex-col quantiv-app-shell">
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
