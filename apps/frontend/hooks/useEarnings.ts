import { useQuery } from '@tanstack/react-query';

interface EarningsEvent {
  date: string;
  actualEPS?: number;
  estimatedEPS?: number;
  actualRevenue?: number;
  estimatedRevenue?: number;
  epsSurprise?: number;
  epsSurprisePercent?: number;
  revenueSurprise?: number;
  revenueSurprisePercent?: number;
  priceMovePercent?: number;
  isUpcoming?: boolean;
  timing?: 'BMO' | 'AMC' | 'UNKNOWN';
}

interface EarningsData {
  nextEarningsDate?: string;
  nextEarningsTime?: 'BMO' | 'AMC' | 'UNKNOWN';
  estimatedEPS?: number;
  estimatedRevenue?: number;
  historicalEarnings: EarningsEvent[];
  stats: {
    avgMove: number;
    avgAbsMove: number;
    beatRate: number;
    avgBeat: number;
    revenueBeatRate?: number;
    avgRevenueBeat?: number;
  };
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
