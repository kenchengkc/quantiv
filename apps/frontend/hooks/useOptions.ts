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

      const res = await fetch(`/api/options-live?symbol=${encodeURIComponent(symbol)}`);

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Failed to fetch options for ${symbol}`);
      }

      const json = await res.json();

      if (!json.success || !json.data) {
        throw new Error(json.error || 'No options data returned');
      }

      const raw = json.data;

      // Normalize the API response into the shape components expect
      const expirations: OptionsChainData['chain']['expirations'] = (raw.expirations || []).map(
        (exp: string) => {
          const dte = Math.max(0, Math.round((new Date(exp).getTime() - Date.now()) / 86_400_000));
          return { date: exp, dte };
        }
      );

      const strikes: OptionsChainData['chain']['strikes'] = raw.strikes || {};

      const quote = raw.quote
        ? {
            last: raw.quote.last ?? 0,
            change: raw.quote.change ?? 0,
            changePercent: raw.quote.changePercent ?? 0,
            name: raw.quote.name ?? symbol,
          }
        : undefined;

      const ivStats: OptionsChainData['ivStats'] = raw.ivStats
        ? {
            current: raw.ivStats.current ?? 0,
            rank: raw.ivStats.rank ?? 0,
            percentile: raw.ivStats.percentile ?? 0,
            high52Week: raw.ivStats.high52Week ?? 0,
            low52Week: raw.ivStats.low52Week ?? 0,
          }
        : undefined;

      // Determine ATM strike from the first expiration
      let atmStrike: number | null = null;
      const spotPrice = quote?.last ?? 0;
      if (spotPrice > 0 && expirations.length > 0) {
        const firstExpStrikes = strikes[expirations[0].date];
        if (firstExpStrikes) {
          const allStrikes = [...new Set(Object.values(firstExpStrikes).map((o) => o.strike))];
          if (allStrikes.length > 0) {
            atmStrike = allStrikes.reduce((best, s) =>
              Math.abs(s - spotPrice) < Math.abs(best - spotPrice) ? s : best
            );
          }
        }
      }

      return {
        chain: { expirations, strikes, quote },
        atmStrike,
        ivStats,
        live_data: raw.live_data,
      };
    },
    enabled: !!symbol,
    staleTime: 10 * 1000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30 * 1000,
    refetchOnWindowFocus: true,
    refetchOnMount: true,
  });
}
