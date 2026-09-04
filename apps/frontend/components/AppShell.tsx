import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Footer } from '@/components/Footer';
import { TickerHoverHost } from '@/components/TickerHoverCard';
import { Topbar } from '@/components/Topbar';
import { Providers } from '@/app/providers';
import styles from './AppShell.module.css';

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
        <div className={`${styles.shell} min-h-screen flex flex-col quantiv-app-shell`}>
          <Topbar authenticated={authenticated} />
          <main className="flex-1">{children}</main>
          <Footer />
        </div>
        <TickerHoverHost />
      </ErrorBoundary>
    </Providers>
  );
}
