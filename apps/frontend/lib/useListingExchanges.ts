'use client';

import { useEffect, useSyncExternalStore } from 'react';
import {
  loadListingExchanges,
  subscribeToListingExchanges,
  getListingExchangesVersion,
} from './listingExchanges';

export function useEnsureListingExchanges(): void {
  useSyncExternalStore(
    subscribeToListingExchanges,
    getListingExchangesVersion,
    () => 0,
  );
  useEffect(() => {
    void loadListingExchanges();
  }, []);
}
