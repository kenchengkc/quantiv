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
    queryKey: ['expectedMove', symbol],
    queryFn: async (): Promise<ExpectedMoveData> => {
      if (!symbol) {
        throw new Error('Symbol is required');
      }

      const response = await fetch(`/api/expected-move?symbol=${symbol}`);
      
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
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // 1 minute
  });
}
