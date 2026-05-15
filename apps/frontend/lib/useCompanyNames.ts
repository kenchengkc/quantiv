'use client';

import { useEffect, useSyncExternalStore } from 'react';
import {
  loadExtendedCompanyNames,
  subscribeToCompanyNames,
  getCompanyNamesVersion,
} from './companyNames';

/**
 * Triggers the one-time EDGAR ticker-names fetch and re-renders the
 * calling component when the data arrives. Idempotent — multiple mounts
 * across the app share a single in-flight promise.
 *
 * Usage: call near the top of any component that renders a long list
 * of company names (screener, calendar, watchlist, symbol page). The
 * sync `companyName(ticker)` helper will then return the EDGAR-fallback
 * name automatically on the post-fetch render pass.
 *
 *   function MyList() {
 *     useEnsureCompanyNames();
 *     return rows.map(r => <Row name={companyName(r.ticker)} />);
 *   }
 *
 * The server snapshot returns 0 so SSR is stable; the client takes over
 * after hydration, triggers the fetch via useEffect, and the
 * useSyncExternalStore subscription bumps a render when names land.
 */
export function useEnsureCompanyNames(): void {
  useSyncExternalStore(
    subscribeToCompanyNames,
    getCompanyNamesVersion,
    () => 0,
  );
  useEffect(() => {
    void loadExtendedCompanyNames();
  }, []);
}
