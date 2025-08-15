import { useQuery } from '@tanstack/react-query';

interface ExpectedMoveData {
  symbol: string;
  spotPrice: number;
  summary: {
    daily?: { move: number; percentage: number; lower: number; upper: number } | null;
    weekly?: { move: number; percentage: number; lower: number; upper: number } | null;
    monthly?: { move: number; percentage: number; lower: number; upper: number } | null;
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

      // Use the new live API endpoint that integrates FMP + Polygon.io + Dolt
      const response = await fetch(`/api/expected-move-live?symbol=${symbol}`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch expected move data: ${response.statusText}`);
      }

      const apiResponse = await response.json();
      
      // Extract data from API response wrapper
      if (!apiResponse.success || !apiResponse.data) {
        throw new Error(apiResponse.error || 'Failed to fetch expected move data');
      }
      
      return apiResponse.data;
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
