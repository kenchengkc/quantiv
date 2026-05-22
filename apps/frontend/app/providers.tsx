'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

// Records the previous in-app pathname into sessionStorage so deep pages
// (like the ticker detail view) can label their back button with the actual
// origin — "Screener" vs "Watchlist" vs the default "Earnings Calendar".
function PrevRouteTracker() {
  const pathname = usePathname();
  const prevRef = useRef<string | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (prevRef.current && prevRef.current !== pathname) {
      window.sessionStorage.setItem('quantiv:prevRoute', prevRef.current);
    }
    prevRef.current = pathname;
  }, [pathname]);
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000, // 1 minute
            refetchOnWindowFocus: false,
            retry: 2,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <PrevRouteTracker />
      {children}
    </QueryClientProvider>
  );
}
