import { useQuery } from '@tanstack/react-query';

// Updated interface to match the backend API response
interface ExpectedMoveData {
  symbol: string;
  timestamp: string;
  forecasts: Array<{
    underlying: string;
    quote_ts: string;
    exp_date: string;
    horizon: string;
    em_baseline: number;
    band68_low: number;
    band68_high: number;
    band95_low?: number;
    band95_high?: number;
  }>;
  live_data: {
    symbol: string;
    price: number;
    change: number;
    change_percent: number;
    volume: number;
    timestamp: string;
  };
  metadata: {
    forecast_count: number;
    horizons_requested: string[];
    has_live_data: boolean;
  };
}

interface UseExpectedMoveParams {
  symbol: string;
}

export function useExpectedMove({ symbol }: UseExpectedMoveParams) {
  return useQuery({
    queryKey: ['expectedMove-live', symbol],
    queryFn: async (): Promise<ExpectedMoveData> => {
      if (!symbol) {
        throw new Error('Symbol is required');
      }

      // Use the ML forecast proxy endpoint
      const response = await fetch(`/api/ml-forecast`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ symbol })
      });
      
      if (!response.ok) {
        throw new Error(`Failed to fetch expected move data: ${response.statusText}`);
      }

      const apiResponse = await response.json();
      
      return apiResponse;
    },
    enabled: !!symbol,
    staleTime: 60 * 60 * 1000, // 1 hour - keep expected move data stable for analysis
    gcTime: 90 * 60 * 1000, // 1.5 hours - persist in cache longer for stability
    refetchInterval: false, // Don't auto-refetch to prevent data loss during analysis
    refetchOnWindowFocus: false, // Don't refetch on focus to maintain stability
    refetchOnMount: false, // Don't refetch on mount if we have cached data
    retry: 3, // Retry failed requests
    retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000) // Exponential backoff
  });
}
