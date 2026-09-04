import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Footer } from '@/components/Footer';
import { ResearchStatusStrip } from '@/components/ResearchStatusStrip';
import { TickerHoverHost } from '@/components/TickerHoverCard';
import { Topbar } from '@/components/Topbar';
import { Providers } from '@/app/providers';

export function AppShell({
  authenticated,
  children,
}: {
  authenticated: boolean;
  children: React.ReactNode;
}) {
  return (
    <Providers>
      <ErrorBoundary>
        <div className="min-h-screen flex flex-col quantiv-app-shell">
          <Topbar authenticated={authenticated} />
          <ResearchStatusStrip />
          <main className="flex-1">{children}</main>
          <Footer />
        </div>
        <TickerHoverHost />
      </ErrorBoundary>
    </Providers>
  );
}
