/**
 * Comprehensive Live Data Service
 * Integrates live APIs (FMP + Polygon.io) for real-time data and Dolt database for historical/recent data
 * 
 * Data Source Strategy:
 * - FMP: Live quotes, earnings, company fundamentals
 * - Polygon.io: Live options chains, real-time Greeks, current IV
 * - Dolt: Historical IV data, recent options data for analysis, backtesting
 */

import DoltService from './doltService';
import PolygonOptionsService from './polygonOptionsService';
import { fmpService } from './fmpService';
import type { EnhancedOptionsChain } from './enhancedLiveDataService';

// Define types locally since they're used across multiple services
interface OptionsChainData {
  symbol: string;
  quote: {
    price: number;
    change: number;
    changePercent: number;
    name: string;
  };
  chain: {
    expirations: string[];
    strikes: Record<string, {
      strike: number;
      call?: {
        bid: number;
        ask: number;
        last: number;
        volume: number;
        openInterest: number;
        impliedVolatility: number;
        delta: number;
        gamma: number;
        theta: number;
        vega: number;
      };
      put?: {
        bid: number;
        ask: number;
        last: number;
        volume: number;
        openInterest: number;
        impliedVolatility: number;
        delta: number;
        gamma: number;
        theta: number;
        vega: number;
      };
    }>;
  };
  ivStats?: {
    rank: number;
    percentile: number;
    current: number;
    high52Week: number;
    low52Week: number;
  };
}

interface ExpectedMoveData {
  symbol: string;
  summary: {
    daily: number;
    weekly: number;
    monthly: number;
  };
  calculations: {
    straddle: {
      daily: number;
      weekly: number;
      monthly: number;
    };
    iv: {
      daily: number;
      weekly: number;
      monthly: number;
    };
  };
}

export interface ComprehensiveLiveDataConfig {
  dolt: {
    endpoint: string;
    database: string;
    branch?: string;
    apiKey?: string;
    endpoints?: {
      chainEndpoint: string;
      ivEndpoint: string;
    };
  };
  apis: {
    polygonApiKey?: string;
    fmpApiKey?: string;
    alphaVantageApiKey?: string;
    finnhubApiKey?: string;
  };
}

class ComprehensiveLiveDataService {
  private static instance: ComprehensiveLiveDataService;
  private doltService: DoltService;
  private polygonService: PolygonOptionsService;

  constructor(config: ComprehensiveLiveDataConfig) {
    this.doltService = DoltService.getInstance(config.dolt);
    this.polygonService = PolygonOptionsService.getInstance();
  }

  static getInstance(config?: ComprehensiveLiveDataConfig): ComprehensiveLiveDataService {
    if (!ComprehensiveLiveDataService.instance) {
      if (!config) {
        throw new Error('ComprehensiveLiveDataService requires configuration on first initialization');
      }
      ComprehensiveLiveDataService.instance = new ComprehensiveLiveDataService(config);
    }
    return ComprehensiveLiveDataService.instance;
  }

  /**
   * Convert EnhancedOptionsChain strikes array to OptionsChainData strikes record
   */
  private convertStrikesToRecord(strikes: EnhancedOptionsChain['strikes']): Record<string, any> {
    const record: Record<string, any> = {};
    
    strikes.forEach(strike => {
      record[strike.strike.toString()] = {
        strike: strike.strike,
        call: strike.call,
        put: strike.put,
      };
    });
    
    return record;
  }

  /**
   * Get comprehensive options chain data combining live APIs and historical Dolt data
   */
  async getOptionsChain(symbol: string, expiry?: string): Promise<OptionsChainData | null> {
    try {
      console.log(`[ComprehensiveLive] Fetching options chain for ${symbol}`);

      // Try Polygon first for real-time data
      let liveChain = null;
      try {
        liveChain = await this.polygonService.getOptionsChain(symbol, expiry);
        console.log(`[ComprehensiveLive] Got live options chain from Polygon for ${symbol}`);
      } catch (error) {
        console.log(`[ComprehensiveLive] Polygon options chain failed for ${symbol}:`, error);
      }

      // Get live quote data from FMP
      let quote = null;
      try {
        quote = await fmpService.fetchQuote(symbol);
        console.log(`[ComprehensiveLive] Got live quote from FMP for ${symbol}`);
      } catch (error) {
        console.log(`[ComprehensiveLive] FMP live quote failed for ${symbol}:`, error);
      }

      // Get IV stats from Dolt database
      let ivStats = null;
      try {
        const doltIVStats = await this.doltService.getCurrentIVStats(symbol);
        if (doltIVStats) {
          ivStats = {
            rank: doltIVStats.rank,
            percentile: doltIVStats.percentile,
            current: doltIVStats.current,
            high52Week: doltIVStats.high52Week,
            low52Week: doltIVStats.low52Week,
          };
          console.log(`[ComprehensiveLive] Got IV stats from Dolt for ${symbol}`);
        }
      } catch (error) {
        console.log(`[ComprehensiveLive] Dolt IV stats failed for ${symbol}:`, error);
      }

      // Get historical IV data from Dolt for sparkline
      let ivHistory: Array<{ date: string; iv: number }> = [];
      try {
        ivHistory = await this.doltService.getIVHistory(symbol, 30);
        console.log(`[ComprehensiveLive] Got ${ivHistory.length} IV history points from Dolt for ${symbol}`);
      } catch (error) {
        console.log(`[ComprehensiveLive] Dolt IV history failed for ${symbol}:`, error);
      }

      // If we have live chain data, enhance it with Dolt IV stats
      if (liveChain && quote) {
        return {
          symbol: symbol.toUpperCase(),
          quote: {
            price: quote.price,
            change: quote.change,
            changePercent: quote.changePercent,
            name: quote.name || `${symbol} Inc.`,
          },
          chain: {
            expirations: [liveChain.expirationDate],
            strikes: this.convertStrikesToRecord(liveChain.strikes),
          },
          ivStats: ivStats || {
            rank: 50,
            percentile: 50,
            current: 25,
            high52Week: 40,
            low52Week: 15,
          },
        };
      }

      // Fallback: construct from available data
      if (quote) {
        return {
          symbol: symbol.toUpperCase(),
          quote: {
            name: quote.name || `${symbol} Inc.`,
            last: quote.price,
            change: quote.change,
            changePercent: quote.changePercent,
          },
          expirations: [],
          strikes: {},
          ivStats: ivStats || {
            rank: 50,
            percentile: 50,
            current: 25,
            high52Week: 40,
            low52Week: 15,
          },
        };
      }

      console.log(`[ComprehensiveLive] No data available for ${symbol}`);
      return null;
    } catch (error) {
      console.error(`[ComprehensiveLive] Failed to get options chain for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get comprehensive expected move data using multiple sources
   */
  async getExpectedMove(symbol: string): Promise<ExpectedMoveData | null> {
    try {
      console.log(`[ComprehensiveLive] Calculating expected move for ${symbol}`);

      // Get current live quote from FMP
      const quote = await fmpService.fetchQuote(symbol);
      if (!quote) {
        console.log(`[ComprehensiveLive] No quote data for ${symbol}`);
        return null;
      }

      // Get options chain for straddle calculation
      const optionsChain = await this.getOptionsChain(symbol);
      if (!optionsChain) {
        console.log(`[ComprehensiveLive] No options chain for ${symbol}`);
        return null;
      }

      // Calculate straddle-based expected move
      const currentPrice = quote.price;
      let straddleMove = 0;
      let straddlePrice = 0;

      // Find ATM options
      const atmStrike = Object.keys(optionsChain.strikes)
        .map(exp => Object.keys(optionsChain.strikes[exp]))
        .flat()
        .map(strike => parseFloat(strike))
        .reduce((closest, strike) => 
          Math.abs(strike - currentPrice) < Math.abs(closest - currentPrice) ? strike : closest
        );

      // Get ATM call and put prices
      for (const expiry of optionsChain.expirations) {
        const strikes = optionsChain.strikes[expiry];
        const callKey = `${atmStrike}_call`;
        const putKey = `${atmStrike}_put`;
        
        if (strikes[callKey] && strikes[putKey]) {
          const callPrice = strikes[callKey].mark || strikes[callKey].last || 0;
          const putPrice = strikes[putKey].mark || strikes[putKey].last || 0;
          straddlePrice = callPrice + putPrice;
          straddleMove = straddlePrice * 0.85; // Approximate expected move
          break;
        }
      }

      // Get historical expected moves from Dolt for accuracy
      const historicalMoves = await this.doltService.getHistoricalExpectedMoves(symbol, 30);
      
      // Calculate confidence based on historical accuracy
      let confidence: 'high' | 'medium' | 'low' = 'medium';
      if (historicalMoves.length > 10) {
        const avgAccuracy = historicalMoves
          .filter(m => m.accuracy !== undefined)
          .reduce((sum, m) => sum + (m.accuracy || 0), 0) / historicalMoves.length;
        
        if (avgAccuracy > 0.8) confidence = 'high';
        else if (avgAccuracy < 0.6) confidence = 'low';
      }

      // Calculate daily, weekly, monthly moves
      const dailyMove = straddleMove / 30;
      const weeklyMove = straddleMove / 4;
      const monthlyMove = straddleMove;

      return {
        symbol: symbol.toUpperCase(),
        summary: {
          daily: {
            move: dailyMove,
            percentage: (dailyMove / currentPrice) * 100,
            lower: currentPrice - dailyMove,
            upper: currentPrice + dailyMove,
          },
          weekly: {
            move: weeklyMove,
            percentage: (weeklyMove / currentPrice) * 100,
            lower: currentPrice - weeklyMove,
            upper: currentPrice + weeklyMove,
          },
          monthly: {
            move: monthlyMove,
            percentage: (monthlyMove / currentPrice) * 100,
            lower: currentPrice - monthlyMove,
            upper: currentPrice + monthlyMove,
          },
        },
        straddle: {
          price: straddlePrice,
          move: straddleMove,
          movePercent: (straddleMove / currentPrice) * 100,
        },
        iv: optionsChain.ivStats,
        confidence,
        method: 'hybrid' as const,
        timeToExpiry: 30,
        underlyingPrice: currentPrice,
        impliedVolatility: optionsChain.ivStats.current / 100,
      };
    } catch (error) {
      console.error(`[ComprehensiveLive] Failed to calculate expected move for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get IV history for sparkline visualization from Dolt database
   */
  async getIVHistory(symbol: string, days: number = 252): Promise<Array<{
    date: string;
    iv: number;
  }>> {
    try {
      const history = await this.doltService.getIVHistory(symbol, days);
      return history.map(h => ({
        date: h.date,
        iv: h.current_iv || 25,
      }));
    } catch (error) {
      console.error(`[ComprehensiveLive] Failed to get IV history for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get comprehensive earnings data from FMP (live data)
   */
  async getEarnings(symbol: string) {
    try {
      return await fmpService.fetchEarningsData(symbol);
    } catch (error) {
      console.error(`[ComprehensiveLive] Failed to get live earnings for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get available symbols from Dolt database
   */
  async getAvailableSymbols(): Promise<string[]> {
    try {
      return await this.doltService.getAvailableSymbols();
    } catch (error) {
      console.error('[ComprehensiveLive] Failed to get available symbols:', error);
      return [];
    }
  }

  /**
   * Health check for all data sources
   */
  async healthCheck(): Promise<{
    dolt: boolean;
    polygon: boolean;
    fmp: boolean;
    overall: boolean;
  }> {
    const health = {
      dolt: false,
      polygon: false,
      fmp: false,
      overall: false,
    };

    try {
      // Test Dolt
      const symbols = await this.doltService.getAvailableSymbols();
      health.dolt = symbols.length > 0;
    } catch (error) {
      console.error('[ComprehensiveLive] Dolt health check failed:', error);
    }

    try {
      // Test FMP live data
      const quote = await fmpService.fetchQuote('AAPL');
      health.fmp = quote !== null;
    } catch (error) {
      console.error('[ComprehensiveLive] FMP live data health check failed:', error);
    }

    try {
      // Test Polygon
      const chain = await this.polygonService.getOptionsChain('AAPL');
      health.polygon = chain !== null;
    } catch (error) {
      console.error('[ComprehensiveLive] Polygon health check failed:', error);
    }

    health.overall = health.dolt && (health.polygon || health.fmp);
    return health;
  }
}

export default ComprehensiveLiveDataService;
