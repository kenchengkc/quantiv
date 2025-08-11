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
    quote: { 
      last: number;
      change: number;
      changePercent: number;
      name: string;
    };
  };
  atmStrike: number | null;
  ivStats: {
    current: number;
    rank: number;
    percentile: number;
    high52Week: number;
    low52Week: number;
  };
}

interface UseOptionsParams {
  symbol: string;
}

export function useOptions({ symbol }: UseOptionsParams) {
  return useQuery({
    queryKey: ['options', symbol],
    queryFn: async (): Promise<OptionsChainData> => {
      if (!symbol) {
        throw new Error('Symbol is required');
      }

      const response = await fetch(`/api/options?symbol=${symbol}`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch options data: ${response.statusText}`);
      }

      const apiResponse = await response.json();
      
      // Extract data from API response wrapper
      if (!apiResponse.success || !apiResponse.data) {
        throw new Error(apiResponse.error || 'Failed to fetch options data');
      }
      
      return apiResponse.data;
    },
    enabled: !!symbol,
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // 1 minute
  });
}
