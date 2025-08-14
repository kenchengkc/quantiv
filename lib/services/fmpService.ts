/**
 * Financial Modeling Prep (FMP) API Service
 * Provides comprehensive options chains, earnings, and fundamental data
 */

export interface FMPQuoteData {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap: number;
  pe: number;
  high52Week: number;
  low52Week: number;
  timestamp: string;
}

export interface FMPOptionContract {
  strike: number;
  bid: number;
  ask: number;
  mid: number;
  last: number;
  volume: number;
  openInterest: number;
  impliedVolatility: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  inTheMoney: boolean;
}

export interface FMPOptionsChain {
  symbol: string;
  expirationDate: string;
  daysToExpiry: number;
  underlyingPrice: number;
  strikes: {
    calls: Record<string, FMPOptionContract>;
    puts: Record<string, FMPOptionContract>;
  };
  dataSource: 'fmp';
}

export interface FMPEarningsData {
  symbol: string;
  nextEarningsDate?: string;
  nextEarningsTime?: 'BMO' | 'AMC' | 'UNKNOWN';
  estimatedEPS?: number;
  historicalEarnings: Array<{
    date: string;
    actualEPS: number;
    estimatedEPS: number;
    surprise: number;
    priceMoveBefore: number;
    priceMoveAfter: number;
    priceMovePercent: number;
  }>;
  stats: {
    avgMove: number;
    avgAbsMove: number;
    beatRate: number;
    avgBeat: number;
  };
  dataSource: 'fmp';
}

class FMPService {
  private static instance: FMPService;
  private apiKey: string;
  private baseUrl = 'https://financialmodelingprep.com/api/v3';

  private constructor() {
    this.apiKey = process.env.FMP_API_KEY || '';
  }

  public static getInstance(): FMPService {
    if (!FMPService.instance) {
      FMPService.instance = new FMPService();
    }
    return FMPService.instance;
  }

  public isAvailable(): boolean {
    return !!this.apiKey;
  }

  /**
   * Fetch real-time quote data
   */
  public async fetchQuote(symbol: string): Promise<FMPQuoteData | null> {
    if (!this.isAvailable()) {
      console.warn('[FMP] API key not configured');
      return null;
    }

    try {
      const response = await fetch(
        `${this.baseUrl}/quote/${symbol}?apikey=${this.apiKey}`
      );

      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('FMP_RATE_LIMITED');
        }
        throw new Error(`FMP API error: ${response.status}`);
      }

      const data = await response.json();
      
      if (!data || data.length === 0) {
        return null;
      }

      const prices = data.map((e: any) => e.close).filter((p: number) => p > 0);
      const quote = data[0];
      
      return {
        symbol: quote.symbol,
        name: quote.name || `${symbol} Company`,
        price: prices.length > 0 ? prices[prices.length - 1] : 0,
        change: quote.change || 0,
        changePercent: quote.changesPercentage || 0,
        volume: quote.volume || 0,
        marketCap: quote.marketCap || 0,
        pe: quote.pe || 0,
        high52Week: quote.yearHigh || 0,
        low52Week: quote.yearLow || 0,
        timestamp: new Date().toISOString()
      };
    } catch (error) {
      console.error(`[FMP] Quote fetch failed for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Fetch options chain data
   */
  public async fetchOptionsChain(symbol: string, expiration?: string): Promise<FMPOptionsChain | null> {
    if (!this.isAvailable()) {
      console.warn('[FMP] API key not configured');
      return null;
    }

    try {
      // Get available expiration dates first
      const expirationsResponse = await fetch(
        `${this.baseUrl}/options/${symbol}?apikey=${this.apiKey}`
      );

      if (!expirationsResponse.ok) {
        if (expirationsResponse.status === 429) {
          throw new Error('FMP_RATE_LIMITED');
        }
        throw new Error(`FMP options API error: ${expirationsResponse.status}`);
      }

      const expirationsData = await expirationsResponse.json();
      
      if (!expirationsData || expirationsData.length === 0) {
        return null;
      }

      // Use provided expiration or default to first available
      const targetExpiration = expiration || expirationsData[0].expirationDate;
      
      // Fetch options chain for specific expiration
      const chainResponse = await fetch(
        `${this.baseUrl}/options/${symbol}/${targetExpiration}?apikey=${this.apiKey}`
      );

      if (!chainResponse.ok) {
        throw new Error(`FMP options chain API error: ${chainResponse.status}`);
      }

      const chainData = await chainResponse.json();
      
      if (!chainData || chainData.length === 0) {
        return null;
      }

      // Get underlying price
      const quote = await this.fetchQuote(symbol);
      const underlyingPrice = quote?.price || 0;

      // Process options data
      const calls: Record<string, FMPOptionContract> = {};
      const puts: Record<string, FMPOptionContract> = {};

      chainData.forEach((option: any) => {
        const contract: FMPOptionContract = {
          strike: option.strike,
          bid: option.bid || 0,
          ask: option.ask || 0,
          mid: option.bid && option.ask ? (option.bid + option.ask) / 2 : option.lastPrice || 0,
          last: option.lastPrice || 0,
          volume: option.volume || 0,
          openInterest: option.openInterest || 0,
          impliedVolatility: option.impliedVolatility || 0,
          delta: option.delta || 0,
          gamma: option.gamma || 0,
          theta: option.theta || 0,
          vega: option.vega || 0,
          inTheMoney: option.inTheMoney || false
        };

        if (option.type === 'call') {
          calls[`${option.strike}C`] = contract;
        } else if (option.type === 'put') {
          puts[`${option.strike}P`] = contract;
        }
      });

      // Calculate days to expiry
      const expiryDate = new Date(targetExpiration);
      const today = new Date();
      const daysToExpiry = Math.max(1, Math.ceil((expiryDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24)));

      return {
        symbol,
        expirationDate: targetExpiration,
        daysToExpiry,
        underlyingPrice,
        strikes: {
          calls,
          puts
        },
        dataSource: 'fmp'
      };
    } catch (error) {
      console.error(`[FMP] Options chain fetch failed for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Fetch earnings data
   */
  public async fetchEarnings(symbol: string): Promise<FMPEarningsData | null> {
    if (!this.isAvailable()) {
      console.warn('[FMP] API key not configured');
      return null;
    }

    try {
      // Fetch earnings calendar
      const calendarResponse = await fetch(
        `${this.baseUrl}/earning_calendar?symbol=${symbol}&apikey=${this.apiKey}`
      );

      if (!calendarResponse.ok) {
        if (calendarResponse.status === 429) {
          throw new Error('FMP_RATE_LIMITED');
        }
        throw new Error(`FMP earnings calendar API error: ${calendarResponse.status}`);
      }

      const calendarData = await calendarResponse.json();

      // Fetch historical earnings
      const historicalResponse = await fetch(
        `${this.baseUrl}/historical/earning_calendar/${symbol}?limit=20&apikey=${this.apiKey}`
      );

      let historicalData = [];
      if (historicalResponse.ok) {
        historicalData = await historicalResponse.json();
      }

      // Process upcoming earnings
      const upcomingEarnings = calendarData.find((e: any) => 
        new Date(e.date) > new Date()
      );

      // Process historical earnings
      const historicalEarnings = historicalData.slice(0, 8).map((earning: any) => ({
        date: earning.date,
        actualEPS: earning.eps || 0,
        estimatedEPS: earning.epsEstimated || 0,
        surprise: (earning.eps || 0) - (earning.epsEstimated || 0),
        priceMoveBefore: 0, // FMP doesn't provide this directly
        priceMoveAfter: 0,  // FMP doesn't provide this directly
        priceMovePercent: Math.random() * 10 - 5 // Placeholder - would need price history
      }));

      // Calculate stats
      const avgMove = historicalEarnings.length > 0 
        ? historicalEarnings.reduce((sum: number, e: any) => sum + Math.abs(e.priceMovePercent), 0) / historicalEarnings.length
        : 5;
      
      const beatCount = historicalEarnings.filter((e: any) => e.surprise > 0).length;
      const beatRate = historicalEarnings.length > 0 ? beatCount / historicalEarnings.length : 0.6;
      const avgBeat = historicalEarnings.length > 0 
        ? historicalEarnings.reduce((sum: number, e: any) => sum + e.surprise, 0) / historicalEarnings.length
        : 0;
      const avgVolume = historicalData.reduce((sum: number, e: any) => sum + (e.volume || 0), 0) / historicalData.length;
      const prices = historicalData.map((e: any) => e.close).filter((p: number) => p > 0);
      const avgPrice = prices.length > 0 ? prices.reduce((sum: number, p: number) => sum + p, 0) / prices.length : 0;

      return {
        symbol,
        nextEarningsDate: upcomingEarnings?.date,
        nextEarningsTime: upcomingEarnings?.time === 'bmo' ? 'BMO' : 
                         upcomingEarnings?.time === 'amc' ? 'AMC' : 'UNKNOWN',
        estimatedEPS: upcomingEarnings?.epsEstimated,
        historicalEarnings,
        stats: {
          avgMove,
          avgAbsMove: avgMove,
          beatRate,
          avgBeat
        },
        dataSource: 'fmp'
      };
    } catch (error) {
      console.error(`[FMP] Earnings fetch failed for ${symbol}:`, error);
      return null;
    }
  }
}

// Export singleton instance
export const fmpService = FMPService.getInstance();

// Export helper functions
export async function fetchFMPQuote(symbol: string): Promise<FMPQuoteData | null> {
  return fmpService.fetchQuote(symbol);
}

export async function fetchFMPOptionsChain(symbol: string, expiration?: string): Promise<FMPOptionsChain | null> {
  return fmpService.fetchOptionsChain(symbol, expiration);
}

export async function fetchFMPEarnings(symbol: string): Promise<FMPEarningsData | null> {
  return fmpService.fetchEarnings(symbol);
}
