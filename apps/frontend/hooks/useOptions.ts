import { useQuery } from '@tanstack/react-query';

interface OptionsChainData {
  chain: {
    expirations: Array<{ date: string; dte: number }>;
    strikes: Record<string, Record<string, {
      strike: number;
      type: 'call' | 'put';
      bid: number | null;
      ask: number | null;
      mark: number;
      volume: number;
      openInterest: number;
      iv: number | null;
      delta: number | null;
      inTheMoney: boolean;
    }>>;
    quote?: { 
      last: number;
      change: number;
      changePercent: number;
      name: string;
    };
  };
  atmStrike: number | null;
  ivStats?: {
    current: number;
    rank: number;
    percentile: number;
    high52Week: number;
    low52Week: number;
  };
  // Add live_data from the backend API
  live_data?: {
    symbol: string;
    price: number;
    change: number;
    change_percent: number;
    volume: number;
    timestamp: string;
  };
}

interface UseOptionsParams {
  symbol: string;
}

export function useOptions({ symbol }: UseOptionsParams) {
  return useQuery({
    queryKey: ['options-live', symbol],
    queryFn: async (): Promise<OptionsChainData> => {
      if (!symbol) {
        throw new Error('Symbol is required');
      }

      // For now, create a mock structure that matches the expected interface
      // In the future, this should be replaced with a proper options chain API
      const mockData: OptionsChainData = {
        chain: {
          expirations: [],
          strikes: {}
        },
        atmStrike: null
      };
      
      return mockData;
    },
    enabled: !!symbol,
    staleTime: 10 * 1000, // 10 seconds - rapid updates for live prices
    gcTime: 5 * 60 * 1000, // 5 minutes - shorter cache for live data
    refetchInterval: 30 * 1000, // 30 seconds - frequent updates for live prices
    refetchOnWindowFocus: true, // Refetch when user returns to tab
    refetchOnMount: true // Always get fresh data on mount
  });
}
