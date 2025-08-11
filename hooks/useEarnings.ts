import { useQuery } from '@tanstack/react-query';

interface EarningsData {
  events: Array<{
    date: string;
    time: 'BMO' | 'AMC' | 'UNKNOWN';
    fiscalQuarter: string;
    historicalMoves?: Array<{
      date: string;
      priceMovePercent: number;
      epsSurprise: number | null;
    }>;
  }>;
  stats: {
    avgMove: number;
    avgAbsMove: number;
    beatRate: number;
    positiveReactionRate: number;
  } | null;
}

interface UseEarningsParams {
  symbol: string;
}

export function useEarnings({ symbol }: UseEarningsParams) {
  return useQuery({
    queryKey: ['earnings', symbol],
    queryFn: async (): Promise<EarningsData> => {
      if (!symbol) {
        throw new Error('Symbol is required');
      }

      const response = await fetch(`/api/earnings?symbol=${symbol}`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch earnings data: ${response.statusText}`);
      }

      const apiResponse = await response.json();
      
      // Extract data from API response wrapper
      if (!apiResponse.success || !apiResponse.data) {
        throw new Error(apiResponse.error || 'Failed to fetch earnings data');
      }
      
      return apiResponse.data;
    },
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000, // 5 minutes (earnings data changes less frequently)
    refetchInterval: 10 * 60 * 1000, // 10 minutes
  });
}
