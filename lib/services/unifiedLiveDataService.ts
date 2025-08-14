/**
 * Unified Live Data Service
 * Integrates FMP, Finnhub, Alpha Vantage, Polygon, and Yahoo Finance
 * NO MOCK DATA - All data comes from live APIs
 */

import { fmpService, FMPQuoteData, FMPOptionsChain, FMPEarningsData } from './fmpService';
import { fetchEnhancedQuote, fetchEnhancedOptionsChain, fetchEnhancedEarnings } from './enhancedLiveDataService';
import yahooFinance from 'yahoo-finance2';

export interface UnifiedQuoteData {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap?: number;
  pe?: number;
  high52Week?: number;
  low52Week?: number;
  dataSource: 'fmp' | 'finnhub' | 'polygon' | 'alpha_vantage' | 'yahoo';
  timestamp: string;
}

export interface UnifiedOptionsChain {
  symbol: string;
  expirationDate: string;
  daysToExpiry: number;
  underlyingPrice: number;
  strikes: Array<{
    strike: number;
    call: {
      bid: number;
      ask: number;
      mid: number;
      last: number;
      volume: number;
      openInterest: number;
      iv: number;
      delta: number;
      gamma: number;
      theta: number;
      vega: number;
      inTheMoney: boolean;
    };
    put: {
      bid: number;
      ask: number;
      mid: number;
      last: number;
      volume: number;
      openInterest: number;
      iv: number;
      delta: number;
      gamma: number;
      theta: number;
      vega: number;
      inTheMoney: boolean;
    };
  }>;
  dataSource: 'fmp' | 'finnhub' | 'polygon' | 'yahoo';
}

export interface UnifiedEarningsData {
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
  dataSource: 'fmp' | 'finnhub' | 'alpha_vantage';
}

class UnifiedLiveDataService {
  private static instance: UnifiedLiveDataService;

  private constructor() {}

  public static getInstance(): UnifiedLiveDataService {
    if (!UnifiedLiveDataService.instance) {
      UnifiedLiveDataService.instance = new UnifiedLiveDataService();
    }
    return UnifiedLiveDataService.instance;
  }

  /**
   * Fetch quote data with intelligent fallback chain
   * Priority: FMP -> Finnhub -> Polygon -> Yahoo Finance
   */
  public async fetchQuote(symbol: string): Promise<UnifiedQuoteData> {
    console.log(`[unified-data] Fetching quote for ${symbol}`);

    // Try FMP first (paid, reliable)
    if (fmpService.isAvailable()) {
      try {
        const fmpQuote = await fmpService.fetchQuote(symbol);
        if (fmpQuote && fmpQuote.price > 0) {
          console.log(`[unified-data] Quote from FMP for ${symbol}`);
          return {
            ...fmpQuote,
            dataSource: 'fmp'
          };
        }
      } catch (error) {
        if (error instanceof Error && error.message.includes('FMP_RATE_LIMITED')) {
          console.warn(`[unified-data] FMP rate limited for ${symbol}, trying fallback`);
        } else {
          console.warn(`[unified-data] FMP quote failed for ${symbol}:`, error);
        }
      }
    }

    // Try enhanced live data service (Finnhub, Polygon, etc.)
    try {
      const enhancedQuote = await fetchEnhancedQuote(symbol);
      if (enhancedQuote && enhancedQuote.price > 0) {
        console.log(`[unified-data] Quote from enhanced service for ${symbol}`);
        return {
          symbol: enhancedQuote.symbol,
          name: `${symbol} Company`, // Enhanced service doesn't always provide name
          price: enhancedQuote.price,
          change: enhancedQuote.change,
          changePercent: enhancedQuote.changePercent,
          volume: enhancedQuote.volume,
          marketCap: enhancedQuote.marketCap,
          pe: enhancedQuote.peRatio,
          high52Week: enhancedQuote.high52Week,
          low52Week: enhancedQuote.low52Week,
          dataSource: enhancedQuote.dataSource as any,
          timestamp: new Date().toISOString()
        };
      }
    } catch (error) {
      console.warn(`[unified-data] Enhanced quote failed for ${symbol}:`, error);
    }

    // Final fallback to Yahoo Finance
    try {
      const yahooQuote = await yahooFinance.quote(symbol);
      console.log(`[unified-data] Quote from Yahoo Finance for ${symbol}`);
      
      return {
        symbol,
        name: yahooQuote.shortName || yahooQuote.longName || `${symbol} Company`,
        price: yahooQuote.regularMarketPrice || 0,
        change: yahooQuote.regularMarketChange || 0,
        changePercent: yahooQuote.regularMarketChangePercent || 0,
        volume: yahooQuote.regularMarketVolume || 0,
        marketCap: yahooQuote.marketCap,
        pe: yahooQuote.trailingPE,
        high52Week: yahooQuote.fiftyTwoWeekHigh,
        low52Week: yahooQuote.fiftyTwoWeekLow,
        dataSource: 'yahoo',
        timestamp: new Date().toISOString()
      };
    } catch (error) {
      console.error(`[unified-data] All quote sources failed for ${symbol}:`, error);
      throw new Error(`Unable to fetch quote data for ${symbol} from any source`);
    }
  }

  /**
   * Fetch options chain with intelligent fallback
   * Priority: FMP -> Enhanced Live Data -> Yahoo Finance
   */
  public async fetchOptionsChain(symbol: string, expiration?: string): Promise<UnifiedOptionsChain> {
    console.log(`[unified-data] Fetching options chain for ${symbol}`);

    // Try FMP first (best options data)
    if (fmpService.isAvailable()) {
      try {
        const fmpChain = await fmpService.fetchOptionsChain(symbol, expiration);
        if (fmpChain && Object.keys(fmpChain.strikes.calls).length > 0) {
          console.log(`[unified-data] Options chain from FMP for ${symbol}`);
          return this.convertFMPChainToUnified(fmpChain);
        }
      } catch (error) {
        if (error instanceof Error && error.message.includes('FMP_RATE_LIMITED')) {
          console.warn(`[unified-data] FMP rate limited for ${symbol}, trying fallback`);
        } else {
          console.warn(`[unified-data] FMP options failed for ${symbol}:`, error);
        }
      }
    }

    // Try enhanced live data service
    try {
      const enhancedChain = await fetchEnhancedOptionsChain(symbol, expiration);
      if (enhancedChain && enhancedChain.strikes && enhancedChain.strikes.length > 0) {
        console.log(`[unified-data] Options chain from enhanced service for ${symbol}`);
        return this.convertEnhancedChainToUnified(enhancedChain);
      }
    } catch (error) {
      console.warn(`[unified-data] Enhanced options failed for ${symbol}:`, error);
    }

    // Final fallback to Yahoo Finance (limited options data)
    try {
      const yahooOptions = await yahooFinance.options(symbol, {});
      if (yahooOptions && yahooOptions.options && yahooOptions.options.length > 0) {
        console.log(`[unified-data] Options chain from Yahoo Finance for ${symbol}`);
        return this.convertYahooOptionsToUnified(yahooOptions, symbol);
      }
    } catch (error) {
      console.warn(`[unified-data] Yahoo options failed for ${symbol}:`, error);
    }

    throw new Error(`Unable to fetch options chain for ${symbol} from any source`);
  }

  /**
   * Fetch earnings data with intelligent fallback
   * Priority: FMP -> Enhanced Live Data
   */
  public async fetchEarnings(symbol: string): Promise<UnifiedEarningsData> {
    console.log(`[unified-data] Fetching earnings for ${symbol}`);

    // Try FMP first (comprehensive earnings data)
    if (fmpService.isAvailable()) {
      try {
        const fmpEarnings = await fmpService.fetchEarnings(symbol);
        if (fmpEarnings) {
          console.log(`[unified-data] Earnings from FMP for ${symbol}`);
          return fmpEarnings;
        }
      } catch (error) {
        if (error instanceof Error && error.message.includes('FMP_RATE_LIMITED')) {
          console.warn(`[unified-data] FMP rate limited for ${symbol}, trying fallback`);
        } else {
          console.warn(`[unified-data] FMP earnings failed for ${symbol}:`, error);
        }
      }
    }

    // Try enhanced live data service (Finnhub, etc.)
    try {
      const enhancedEarnings = await fetchEnhancedEarnings(symbol);
      if (enhancedEarnings) {
        console.log(`[unified-data] Earnings from enhanced service for ${symbol}`);
        return {
          symbol: enhancedEarnings.symbol,
          nextEarningsDate: enhancedEarnings.nextEarningsDate,
          nextEarningsTime: enhancedEarnings.nextEarningsTime,
          estimatedEPS: enhancedEarnings.estimatedEPS,
          historicalEarnings: enhancedEarnings.historicalEarnings.map((earning: any) => ({
            date: earning.date,
            actualEPS: earning.actualEPS || 0,
            estimatedEPS: earning.estimatedEPS || 0,
            surprise: earning.surprise || 0,
            priceMoveBefore: earning.priceMoveBefore,
            priceMoveAfter: earning.priceMoveAfter,
            priceMovePercent: earning.priceMovePercent
          })),
          stats: enhancedEarnings.stats,
          dataSource: enhancedEarnings.dataSource as any
        };
      }
    } catch (error) {
      console.warn(`[unified-data] Enhanced earnings failed for ${symbol}:`, error);
    }

    throw new Error(`Unable to fetch earnings data for ${symbol} from any source`);
  }

  /**
   * Convert FMP options chain to unified format
   */
  private convertFMPChainToUnified(fmpChain: FMPOptionsChain): UnifiedOptionsChain {
    const strikes: UnifiedOptionsChain['strikes'] = [];

    // Get all unique strike prices
    const callStrikes = Object.keys(fmpChain.strikes.calls).map(k => parseFloat(k.replace('C', '')));
    const putStrikes = Object.keys(fmpChain.strikes.puts).map(k => parseFloat(k.replace('P', '')));
    const allStrikes = [...new Set([...callStrikes, ...putStrikes])].sort((a, b) => a - b);

    allStrikes.forEach(strike => {
      const call = fmpChain.strikes.calls[`${strike}C`];
      const put = fmpChain.strikes.puts[`${strike}P`];

      if (call && put) {
        strikes.push({
          strike,
          call: {
            bid: call.bid,
            ask: call.ask,
            mid: call.mid,
            last: call.last,
            volume: call.volume,
            openInterest: call.openInterest,
            iv: call.impliedVolatility,
            delta: call.delta,
            gamma: call.gamma,
            theta: call.theta,
            vega: call.vega,
            inTheMoney: call.inTheMoney
          },
          put: {
            bid: put.bid,
            ask: put.ask,
            mid: put.mid,
            last: put.last,
            volume: put.volume,
            openInterest: put.openInterest,
            iv: put.impliedVolatility,
            delta: put.delta,
            gamma: put.gamma,
            theta: put.theta,
            vega: put.vega,
            inTheMoney: put.inTheMoney
          }
        });
      }
    });

    return {
      symbol: fmpChain.symbol,
      expirationDate: fmpChain.expirationDate,
      daysToExpiry: fmpChain.daysToExpiry,
      underlyingPrice: fmpChain.underlyingPrice,
      strikes,
      dataSource: 'fmp'
    };
  }

  /**
   * Convert enhanced chain to unified format
   */
  private convertEnhancedChainToUnified(enhancedChain: any): UnifiedOptionsChain {
    const strikes: UnifiedOptionsChain['strikes'] = [];

    enhancedChain.strikes.forEach((strike: any) => {
      strikes.push({
        strike: strike.strike,
        call: {
          bid: strike.call.bid,
          ask: strike.call.ask,
          mid: (strike.call.bid + strike.call.ask) / 2,
          last: strike.call.last,
          volume: strike.call.volume,
          openInterest: strike.call.openInterest,
          iv: strike.call.impliedVolatility,
          delta: strike.call.delta,
          gamma: strike.call.gamma,
          theta: strike.call.theta,
          vega: strike.call.vega,
          inTheMoney: strike.strike < enhancedChain.underlyingPrice
        },
        put: {
          bid: strike.put.bid,
          ask: strike.put.ask,
          mid: (strike.put.bid + strike.put.ask) / 2,
          last: strike.put.last,
          volume: strike.put.volume,
          openInterest: strike.put.openInterest,
          iv: strike.put.impliedVolatility,
          delta: strike.put.delta,
          gamma: strike.put.gamma,
          theta: strike.put.theta,
          vega: strike.put.vega,
          inTheMoney: strike.strike > enhancedChain.underlyingPrice
        }
      });
    });

    return {
      symbol: enhancedChain.symbol,
      expirationDate: enhancedChain.expirationDate,
      daysToExpiry: enhancedChain.daysToExpiry,
      underlyingPrice: enhancedChain.underlyingPrice,
      strikes,
      dataSource: enhancedChain.dataSource
    };
  }

  /**
   * Convert Yahoo options to unified format
   */
  private convertYahooOptionsToUnified(yahooOptions: any, symbol: string): UnifiedOptionsChain {
    const strikes: UnifiedOptionsChain['strikes'] = [];
    const firstExpiry = yahooOptions.options[0];
    
    if (!firstExpiry) {
      throw new Error('No options data available from Yahoo Finance');
    }

    const calls = firstExpiry.calls || [];
    const puts = firstExpiry.puts || [];
    
    // Get all unique strikes
    const allStrikes = [...new Set([
      ...calls.map((c: any) => c.strike),
      ...puts.map((p: any) => p.strike)
    ])].sort((a, b) => a - b);

    allStrikes.forEach(strike => {
      const call = calls.find((c: any) => c.strike === strike);
      const put = puts.find((p: any) => p.strike === strike);

      if (call && put) {
        strikes.push({
          strike,
          call: {
            bid: call.bid || 0,
            ask: call.ask || 0,
            mid: call.bid && call.ask ? (call.bid + call.ask) / 2 : call.lastPrice || 0,
            last: call.lastPrice || 0,
            volume: call.volume || 0,
            openInterest: call.openInterest || 0,
            iv: call.impliedVolatility || 0,
            delta: 0, // Yahoo doesn't provide Greeks
            gamma: 0,
            theta: 0,
            vega: 0,
            inTheMoney: call.inTheMoney || false
          },
          put: {
            bid: put.bid || 0,
            ask: put.ask || 0,
            mid: put.bid && put.ask ? (put.bid + put.ask) / 2 : put.lastPrice || 0,
            last: put.lastPrice || 0,
            volume: put.volume || 0,
            openInterest: put.openInterest || 0,
            iv: put.impliedVolatility || 0,
            delta: 0,
            gamma: 0,
            theta: 0,
            vega: 0,
            inTheMoney: put.inTheMoney || false
          }
        });
      }
    });

    // Calculate days to expiry
    const expiryDate = new Date(firstExpiry.expirationDate * 1000);
    const today = new Date();
    const daysToExpiry = Math.max(1, Math.ceil((expiryDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24)));

    return {
      symbol,
      expirationDate: expiryDate.toISOString().split('T')[0],
      daysToExpiry,
      underlyingPrice: yahooOptions.underlyingSymbol?.regularMarketPrice || 0,
      strikes,
      dataSource: 'yahoo'
    };
  }
}

// Export singleton instance
export const unifiedLiveDataService = UnifiedLiveDataService.getInstance();

// Export helper functions
export async function fetchUnifiedQuote(symbol: string): Promise<UnifiedQuoteData> {
  return unifiedLiveDataService.fetchQuote(symbol);
}

export async function fetchUnifiedOptionsChain(symbol: string, expiration?: string): Promise<UnifiedOptionsChain> {
  return unifiedLiveDataService.fetchOptionsChain(symbol, expiration);
}

export async function fetchUnifiedEarnings(symbol: string): Promise<UnifiedEarningsData> {
  return unifiedLiveDataService.fetchEarnings(symbol);
}
