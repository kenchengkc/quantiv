import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

export interface WatchlistItem {
  symbol: string;
  name?: string;
  price?: number;
  change?: number;
  changePercent?: number;
  addedAt: number;
}

const WATCHLIST_KEY = 'quantiv_watchlist';
const WATCHLIST_QUERY_KEY = ['watchlist'];

// Helper to get watchlist from localStorage
const getStoredWatchlist = (): WatchlistItem[] => {
  if (typeof window === 'undefined') return [];
  
  try {
    const stored = localStorage.getItem(WATCHLIST_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch (error) {
    console.error('Error reading watchlist from localStorage:', error);
    return [];
  }
};

// Helper to save watchlist to localStorage
const saveWatchlist = (watchlist: WatchlistItem[]): void => {
  if (typeof window === 'undefined') return;
  
  try {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchlist));
  } catch (error) {
    console.error('Error saving watchlist to localStorage:', error);
  }
};

export const useWatchlist = () => {
  const queryClient = useQueryClient();
  
  // Query for getting watchlist
  const { data: watchlist = [], refetch } = useQuery({
    queryKey: WATCHLIST_QUERY_KEY,
    queryFn: getStoredWatchlist,
    staleTime: 0, // Always check localStorage
  });
  
  // Mutation for adding to watchlist
  const addToWatchlist = useMutation({
    mutationFn: async (item: Omit<WatchlistItem, 'addedAt'>) => {
      const currentWatchlist = getStoredWatchlist();
      
      // Check if already exists
      if (currentWatchlist.some(w => w.symbol === item.symbol)) {
        throw new Error(`${item.symbol} is already in watchlist`);
      }
      
      const newItem: WatchlistItem = {
        ...item,
        addedAt: Date.now(),
      };
      
      const updatedWatchlist = [...currentWatchlist, newItem];
      saveWatchlist(updatedWatchlist);
      return updatedWatchlist;
    },
    onSuccess: (updatedWatchlist) => {
      queryClient.setQueryData(WATCHLIST_QUERY_KEY, updatedWatchlist);
    },
  });
  
  // Mutation for removing from watchlist
  const removeFromWatchlist = useMutation({
    mutationFn: async (symbol: string) => {
      const currentWatchlist = getStoredWatchlist();
      const updatedWatchlist = currentWatchlist.filter(w => w.symbol !== symbol);
      saveWatchlist(updatedWatchlist);
      return updatedWatchlist;
    },
    onSuccess: (updatedWatchlist) => {
      queryClient.setQueryData(WATCHLIST_QUERY_KEY, updatedWatchlist);
    },
  });
  
  // Toggle function for convenience
  const toggleWatchlist = useCallback((item: Omit<WatchlistItem, 'addedAt'>) => {
    const isInWatchlist = watchlist.some(w => w.symbol === item.symbol);
    
    if (isInWatchlist) {
      removeFromWatchlist.mutate(item.symbol);
    } else {
      addToWatchlist.mutate(item);
    }
  }, [watchlist, addToWatchlist, removeFromWatchlist]);
  
  // Check if symbol is in watchlist
  const isInWatchlist = useCallback((symbol: string) => {
    return watchlist.some(w => w.symbol === symbol);
  }, [watchlist]);
  
  // Clear entire watchlist
  const clearWatchlist = useMutation({
    mutationFn: async () => {
      saveWatchlist([]);
      return [];
    },
    onSuccess: () => {
      queryClient.setQueryData(WATCHLIST_QUERY_KEY, []);
    },
  });
  
  // Update watchlist item (for price updates)
  const updateWatchlistItem = useMutation({
    mutationFn: async (updatedItem: WatchlistItem) => {
      const currentWatchlist = getStoredWatchlist();
      const updatedWatchlist = currentWatchlist.map(item =>
        item.symbol === updatedItem.symbol ? { ...item, ...updatedItem } : item
      );
      saveWatchlist(updatedWatchlist);
      return updatedWatchlist;
    },
    onSuccess: (updatedWatchlist) => {
      queryClient.setQueryData(WATCHLIST_QUERY_KEY, updatedWatchlist);
    },
  });
  
  return {
    watchlist,
    addToWatchlist: addToWatchlist.mutate,
    removeFromWatchlist: removeFromWatchlist.mutate,
    toggleWatchlist,
    isInWatchlist,
    clearWatchlist: clearWatchlist.mutate,
    updateWatchlistItem: updateWatchlistItem.mutate,
    isAdding: addToWatchlist.isPending,
    isRemoving: removeFromWatchlist.isPending,
    refetch,
  };
};
