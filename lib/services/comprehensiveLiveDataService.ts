/**
 * Comprehensive Live Data Service
 * Integrates live APIs (FMP + Polygon.io) for real-time data and Local SQLite for historical/recent data
 * 
 * Data Source Strategy:
 * - FMP: Live quotes, earnings, company fundamentals
 * - Polygon.io: Live options chains, real-time Greeks, current IV
 * - Local SQLite: Historical IV data and metadata via LocalDoltService
 */

import PolygonOptionsService from './polygonOptionsService';
import { fmpService } from './fmpService';
import type { EnhancedOptionsChain as PolygonEnhancedOptionsChain } from './polygonOptionsService';
import { localDoltService } from './localDoltService';

// Define types locally since they're used across multiple services
interface OptionsChainData {
  symbol: string;
  quote: {
    last: number;
    change: number;
    changePercent: number;
    name: string;
  };
  expirations: string[];
  strikes: Record<string, Record<string, {
    bid: number;
    ask: number;
    last: number;
    mark?: number;
    volume: number;
    openInterest: number;
    impliedVolatility: number;
    delta?: number;
    gamma?: number;
    theta?: number;
    vega?: number;
  }>>;
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
    daily: { move: number; percentage: number; lower: number; upper: number };
    weekly: { move: number; percentage: number; lower: number; upper: number };
    monthly: { move: number; percentage: number; lower: number; upper: number };
  };
  straddle: { price: number; move: number; movePercent: number };
  iv?: {
    rank: number;
    percentile: number;
    current: number;
    high52Week: number;
    low52Week: number;
  };
  confidence: 'high' | 'medium' | 'low';
  method: 'hybrid';
  timeToExpiry: number;
  underlyingPrice: number;
  impliedVolatility: number;
}

export interface ComprehensiveLiveDataConfig {
  apis?: {
    polygonApiKey?: string;
    fmpApiKey?: string;
    alphaVantageApiKey?: string;
    finnhubApiKey?: string;
  };
}

class ComprehensiveLiveDataService {
  private static instance: ComprehensiveLiveDataService;
  private polygonService: PolygonOptionsService;
  private localDb = localDoltService;

  constructor(_: ComprehensiveLiveDataConfig = {}) {
    this.polygonService = PolygonOptionsService.getInstance();
  }

  static getInstance(config?: ComprehensiveLiveDataConfig): ComprehensiveLiveDataService {
    if (!ComprehensiveLiveDataService.instance) {
      ComprehensiveLiveDataService.instance = new ComprehensiveLiveDataService(config);
    }
    return ComprehensiveLiveDataService.instance;
  }

  /**
   * Convert a strike array (call/put per strike) into a keyed record for a single expiration.
   * Keys follow the pattern `${strike}_call` and `${strike}_put`.
   */
  private convertStrikeArrayToQuoteRecord(
    strikes: PolygonEnhancedOptionsChain['expirations'][number]['strikes']
  ): Record<string, any> {
    const record: Record<string, any> = {};

    strikes.forEach((s) => {
      const strikeKey = s.strike.toString();

      if (s.call) {
        const mark = s.call.bid && s.call.ask ? (s.call.bid + s.call.ask) / 2 : s.call.last || 0;
        record[`${strikeKey}_call`] = {
          bid: s.call.bid,
          ask: s.call.ask,
          last: s.call.last,
          mark,
          volume: s.call.volume,
          openInterest: s.call.openInterest,
          impliedVolatility: s.call.impliedVolatility,
          delta: s.call.delta,
          gamma: s.call.gamma,
          theta: s.call.theta,
          vega: s.call.vega,
        };
      }

      if (s.put) {
        const mark = s.put.bid && s.put.ask ? (s.put.bid + s.put.ask) / 2 : s.put.last || 0;
        record[`${strikeKey}_put`] = {
          bid: s.put.bid,
          ask: s.put.ask,
          last: s.put.last,
          mark,
          volume: s.put.volume,
          openInterest: s.put.openInterest,
          impliedVolatility: s.put.impliedVolatility,
          delta: s.put.delta,
          gamma: s.put.gamma,
          theta: s.put.theta,
          vega: s.put.vega,
        };
      }
    });

    return record;
  }

  /**
   * Get comprehensive options chain data combining live APIs and local historical data
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

      // Get IV stats/history from local SQLite
      let ivStats = null as OptionsChainData['ivStats'] | null;
      try {
        const stats = await this.localDb.getIVStats(symbol);
        const history = await this.localDb.getIVHistory(symbol, 252);
        const ivValues = history.map(h => h.iv);
        const high52Week = ivValues.length ? Math.max(...ivValues) : 0;
        const low52Week = ivValues.length ? Math.min(...ivValues) : 0;
        if (stats) {
          ivStats = {
            rank: stats.rank,
            percentile: stats.percentile,
            current: stats.current,
            high52Week,
            low52Week,
          };
          console.log(`[ComprehensiveLive] Got IV stats from local DB for ${symbol}`);
        } else if (ivValues.length) {
          const current = ivValues[ivValues.length - 1];
          const percentile = Math.round((ivValues.filter(v => v <= current).length / ivValues.length) * 100);
          ivStats = {
            rank: percentile,
            percentile,
            current,
            high52Week,
            low52Week,
          };
        }
      } catch (error) {
        console.log(`[ComprehensiveLive] Local IV stats/history failed for ${symbol}:`, error);
      }

      // If we have live chain data, enhance it with Dolt IV stats
      if (liveChain && quote) {
        // Normalize expirations and strikes to the API schema
        const expirations = Array.isArray((liveChain as any).expirations)
          ? (liveChain as PolygonEnhancedOptionsChain).expirations.map((e) => e.date)
          : (liveChain as any).expirationDate
            ? [(liveChain as any).expirationDate]
            : [];

        const strikesByExpiry: Record<string, Record<string, any>> = {};
        if (Array.isArray((liveChain as any).expirations)) {
          (liveChain as PolygonEnhancedOptionsChain).expirations
            .filter((e) => !expiry || e.date === expiry)
            .forEach((e) => {
              strikesByExpiry[e.date] = this.convertStrikeArrayToQuoteRecord(e.strikes);
            });
        } else if (Array.isArray((liveChain as any).strikes) && expirations[0]) {
          strikesByExpiry[expirations[0]] = this.convertStrikeArrayToQuoteRecord((liveChain as any).strikes);
        }

        return {
          symbol: symbol.toUpperCase(),
          quote: {
            last: quote.price,
            change: quote.change,
            changePercent: quote.changePercent,
            name: quote.name || `${symbol} Inc.`,
          },
          expirations,
          strikes: strikesByExpiry,
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
   * Get comprehensive expected move data using live data only (no Dolt)
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
      let timeToExpiry = 30;

      // Use first available expiration for ATM selection
      const firstExpiry = optionsChain.expirations[0];
      if (firstExpiry && optionsChain.strikes[firstExpiry]) {
        const expDateMs = new Date(firstExpiry).getTime();
        if (!Number.isNaN(expDateMs)) {
          timeToExpiry = Math.max(1, Math.ceil((expDateMs - Date.now()) / (1000 * 60 * 60 * 24)));
        }

        const strikeKeys = Object.keys(optionsChain.strikes[firstExpiry]);
        const uniqueStrikes = Array.from(new Set(
          strikeKeys.map(k => parseFloat(k.split('_')[0])).filter(n => !Number.isNaN(n))
        )) as number[];

        if (uniqueStrikes.length > 0) {
          const atmStrike = uniqueStrikes.reduce((closest, s) =>
            Math.abs(s - currentPrice) < Math.abs(closest - currentPrice) ? s : closest,
            uniqueStrikes[0]
          );

          const callKey = `${atmStrike}_call`;
          const putKey = `${atmStrike}_put`;
          const call = optionsChain.strikes[firstExpiry][callKey];
          const put = optionsChain.strikes[firstExpiry][putKey];
          if (call && put) {
            const callPrice = (call.mark ?? call.last) || 0;
            const putPrice = (put.mark ?? put.last) || 0;
            straddlePrice = callPrice + putPrice;
            straddleMove = straddlePrice * 0.85; // Approximate expected move
          }
        }
      }

      // Confidence heuristic based on data availability
      const confidence: 'high' | 'medium' | 'low' = straddlePrice > 0 ? 'medium' : 'low';

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
          movePercent: currentPrice > 0 ? (straddleMove / currentPrice) * 100 : 0,
        },
        iv: optionsChain.ivStats,
        confidence,
        method: 'hybrid',
        timeToExpiry,
        underlyingPrice: currentPrice,
        impliedVolatility: (optionsChain.ivStats?.current ?? 0) / 100,
      };
    } catch (error) {
      console.error(`[ComprehensiveLive] Failed to calculate expected move for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get IV history for sparkline visualization from local database
   */
  async getIVHistory(symbol: string, days: number = 252): Promise<Array<{
    date: string;
    iv: number;
  }>> {
    try {
      const history = await this.localDb.getIVHistory(symbol, days);
      return history.map(h => ({
        date: h.date,
        iv: h.iv || 25,
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
   * Get available symbols from local database
   */
  async getAvailableSymbols(): Promise<string[]> {
    try {
      return await this.localDb.getAvailableSymbols();
    } catch (error) {
      console.error('[ComprehensiveLive] Failed to get available symbols:', error);
      return [];
    }
  }

  /**
   * Health check for all data sources
   */
  async healthCheck(): Promise<{
    localDb: boolean;
    polygon: boolean;
    fmp: boolean;
    overall: boolean;
  }> {
    const health = {
      localDb: false,
      polygon: false,
      fmp: false,
      overall: false,
    };

    try {
      // Test local DB
      const symbols = await this.localDb.getAvailableSymbols();
      health.localDb = symbols.length > 0;
    } catch (error) {
      console.error('[ComprehensiveLive] Local DB health check failed:', error);
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

    health.overall = health.localDb && (health.polygon || health.fmp);
    return health;
  }
}

export default ComprehensiveLiveDataService;
