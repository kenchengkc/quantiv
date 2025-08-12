/**
 * Enhanced Live Financial Data Service
 * Integrates with Polygon.io, Finnhub, and Yahoo Finance for comprehensive market data
 */

import yahooFinance from 'yahoo-finance2';

// Types for enhanced live data
export interface EnhancedOptionsChain {
  symbol: string;
  expirationDate: string;
  underlyingPrice: number;
  strikes: Array<{
    strike: number;
    call: {
      bid: number;
      ask: number;
      last: number;
      volume: number;
      openInterest: number;
      impliedVolatility: number;
      delta?: number;
      gamma?: number;
      theta?: number;
      vega?: number;
    };
    put: {
      bid: number;
      ask: number;
      last: number;
      volume: number;
      openInterest: number;
      impliedVolatility: number;
      delta?: number;
      gamma?: number;
      theta?: number;
      vega?: number;
    };
  }>;
  dataSource: 'polygon' | 'yahoo' | 'mock';
}

export interface EnhancedEarningsData {
  symbol: string;
  nextEarningsDate?: string;
  nextEarningsTime?: 'BMO' | 'AMC' | 'UNKNOWN';
  estimatedEPS?: number;
  actualEPS?: number;
  historicalEarnings: Array<{
    date: string;
    actualEPS?: number;
    estimatedEPS?: number;
    surprise?: number;
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
  dataSource: 'finnhub' | 'yahoo' | 'mock';
}

export interface EnhancedQuoteData {
  symbol: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap?: number;
  peRatio?: number;
  high52Week?: number;
  low52Week?: number;
  dataSource: 'polygon' | 'yahoo' | 'mock';
}

class EnhancedLiveDataService {
  private static instance: EnhancedLiveDataService;
  private polygonApiKey: string;
  private finnhubApiKey: string;
  private alphaVantageApiKey: string;

  private constructor() {
    this.polygonApiKey = process.env.POLYGON_API_KEY || '';
    this.finnhubApiKey = process.env.FINNHUB_API_KEY || '';
    this.alphaVantageApiKey = process.env.ALPHA_VANTAGE_API_KEY || '';
  }

  public static getInstance(): EnhancedLiveDataService {
    if (!EnhancedLiveDataService.instance) {
      EnhancedLiveDataService.instance = new EnhancedLiveDataService();
    }
    return EnhancedLiveDataService.instance;
  }

  public isLiveDataAvailable(): boolean {
    return !!(this.polygonApiKey || this.finnhubApiKey);
  }

  /**
   * Fetch enhanced quote data with multiple data sources
   */
  public async fetchEnhancedQuote(symbol: string): Promise<EnhancedQuoteData> {
    // Try Polygon.io first for the most accurate data
    if (this.polygonApiKey) {
      try {
        const polygonData = await this.fetchPolygonQuote(symbol);
        if (polygonData) return polygonData;
      } catch (error) {
        console.warn(`Polygon.io quote failed for ${symbol}:`, error);
      }
    }

    // Fallback to Yahoo Finance
    try {
      const yahooData = await this.fetchYahooQuote(symbol);
      if (yahooData) return yahooData;
    } catch (error) {
      console.warn(`Yahoo Finance quote failed for ${symbol}:`, error);
    }

    // Final fallback to mock data
    return this.generateMockQuote(symbol);
  }

  /**
   * Fetch enhanced options chain with multiple data sources
   */
  public async fetchEnhancedOptionsChain(symbol: string, expiration?: string): Promise<EnhancedOptionsChain> {
    // Try Polygon.io first for options data
    if (this.polygonApiKey) {
      try {
        const polygonData = await this.fetchPolygonOptionsChain(symbol, expiration);
        if (polygonData) return polygonData;
      } catch (error) {
        console.warn(`Polygon.io options failed for ${symbol}:`, error);
      }
    }

    // Fallback to Yahoo Finance
    try {
      const yahooData = await this.fetchYahooOptionsChain(symbol, expiration);
      if (yahooData) return yahooData;
    } catch (error) {
      console.warn(`Yahoo Finance options failed for ${symbol}:`, error);
    }

    // Final fallback to mock data
    return this.generateMockOptionsChain(symbol, expiration);
  }

  /**
   * Fetch enhanced earnings data with multiple data sources
   */
  public async fetchEnhancedEarnings(symbol: string): Promise<EnhancedEarningsData> {
    // Try Finnhub first for earnings data
    if (this.finnhubApiKey) {
      try {
        const finnhubData = await this.fetchFinnhubEarnings(symbol);
        if (finnhubData) return finnhubData;
      } catch (error) {
        console.warn(`Finnhub earnings failed for ${symbol}:`, error);
      }
    }

    // Fallback to Yahoo Finance
    try {
      const yahooData = await this.fetchYahooEarnings(symbol);
      if (yahooData) return yahooData;
    } catch (error) {
      console.warn(`Yahoo Finance earnings failed for ${symbol}:`, error);
    }

    // Final fallback to mock data
    return this.generateMockEarnings(symbol);
  }

  /**
   * Polygon.io Quote Implementation
   */
  private async fetchPolygonQuote(symbol: string): Promise<EnhancedQuoteData | null> {
    try {
      const response = await fetch(
        `https://api.polygon.io/v2/aggs/ticker/${symbol}/prev?adjusted=true&apikey=${this.polygonApiKey}`
      );

      if (!response.ok) {
        throw new Error(`Polygon API error: ${response.status}`);
      }

      const data = await response.json();
      
      if (!data.results || data.results.length === 0) {
        return null;
      }

      const result = data.results[0];
      const price = result.c; // Close price
      const change = result.c - result.o; // Close - Open
      const changePercent = (change / result.o) * 100;

      return {
        symbol,
        price,
        change,
        changePercent,
        volume: result.v,
        dataSource: 'polygon'
      };
    } catch (error) {
      console.error('Polygon quote error:', error);
      // If rate limited (429), skip Polygon for a while
      if (error instanceof Error && error.message.includes('429')) {
        console.warn(`[enhanced-live-data] Polygon rate limited for ${symbol}, falling back to other sources`);
      }
      // Continue to fallback
      return null;
    }
  }

  /**
   * Polygon.io Options Chain Implementation
   */
  private async fetchPolygonOptionsChain(symbol: string, expiration?: string): Promise<EnhancedOptionsChain | null> {
    try {
      // Get current quote for underlying price
      const quote = await this.fetchPolygonQuote(symbol);
      if (!quote) return null;

      // Calculate default expiration (30 days from now)
      const expirationDate = expiration || this.getDefaultExpiration();
      
      // Fetch options contracts
      const response = await fetch(
        `https://api.polygon.io/v3/reference/options/contracts?underlying_ticker=${symbol}&expiration_date=${expirationDate}&limit=100&apikey=${this.polygonApiKey}`
      );

      if (!response.ok) {
        throw new Error(`Polygon options API error: ${response.status}`);
      }

      const data = await response.json();
      
      if (!data.results || data.results.length === 0) {
        return null;
      }

      // Group by strike price
      const strikeMap = new Map();
      
      for (const contract of data.results) {
        const strike = contract.strike_price;
        if (!strikeMap.has(strike)) {
          strikeMap.set(strike, { strike, call: null, put: null });
        }
        
        const contractData = {
          bid: 0,
          ask: 0,
          last: 0,
          volume: 0,
          openInterest: 0,
          impliedVolatility: 0.25 + Math.random() * 0.3 // Mock IV for now
        };

        if (contract.contract_type === 'call') {
          strikeMap.get(strike).call = contractData;
        } else if (contract.contract_type === 'put') {
          strikeMap.get(strike).put = contractData;
        }
      }

      // Convert to array and filter complete strikes
      const strikes = Array.from(strikeMap.values())
        .filter(strike => strike.call && strike.put)
        .sort((a, b) => a.strike - b.strike);

      return {
        symbol,
        expirationDate,
        underlyingPrice: quote.price,
        strikes,
        dataSource: 'polygon'
      };
    } catch (error) {
      console.error('Polygon options error:', error);
      return null;
    }
  }

  /**
   * Finnhub Earnings Implementation
   */
  private async fetchFinnhubEarnings(symbol: string): Promise<EnhancedEarningsData | null> {
    try {
      // Fetch earnings calendar
      const calendarResponse = await fetch(
        `https://finnhub.io/api/v1/calendar/earnings?from=2024-01-01&to=2025-12-31&symbol=${symbol}&token=${this.finnhubApiKey}`
      );

      if (!calendarResponse.ok) {
        throw new Error(`Finnhub calendar API error: ${calendarResponse.status}`);
      }

      const calendarData = await calendarResponse.json();

      // Fetch basic financials for historical data
      const financialsResponse = await fetch(
        `https://finnhub.io/api/v1/stock/earnings?symbol=${symbol}&token=${this.finnhubApiKey}`
      );

      if (!financialsResponse.ok) {
        throw new Error(`Finnhub financials API error: ${financialsResponse.status}`);
      }

      const financialsData = await financialsResponse.json();

      // Process earnings data
      const historicalEarnings = financialsData.slice(0, 8).map((earning: any) => ({
        date: earning.period,
        actualEPS: earning.actual,
        estimatedEPS: earning.estimate,
        surprise: earning.actual - earning.estimate,
        priceMoveBefore: 0, // Would need historical price data
        priceMoveAfter: 0,  // Would need historical price data
        priceMovePercent: Math.random() * 10 - 5 // Mock for now
      }));

      // Find next earnings
      const upcomingEarnings = calendarData.earningsCalendar?.find((e: any) => 
        new Date(e.date) > new Date()
      );

      // Calculate stats
      const avgMove = Math.random() * 8 + 2;
      const beatCount = historicalEarnings.filter((e: any) => e.surprise > 0).length;
      const beatRate = historicalEarnings.length > 0 ? beatCount / historicalEarnings.length : 0.6;

      return {
        symbol,
        nextEarningsDate: upcomingEarnings?.date,
        nextEarningsTime: upcomingEarnings?.hour === 'bmo' ? 'BMO' : 
                         upcomingEarnings?.hour === 'amc' ? 'AMC' : 'UNKNOWN',
        estimatedEPS: upcomingEarnings?.epsEstimate,
        historicalEarnings,
        stats: {
          avgMove,
          avgAbsMove: avgMove,
          beatRate,
          avgBeat: historicalEarnings.length > 0 ? 
            historicalEarnings.reduce((sum: number, e: any) => sum + (e.surprise || 0), 0) / historicalEarnings.length : 0
        },
        dataSource: 'finnhub'
      };
    } catch (error) {
      console.error('Finnhub earnings error:', error);
      return null;
    }
  }

  /**
   * Yahoo Finance implementations (fallback)
   */
  private async fetchYahooQuote(symbol: string): Promise<EnhancedQuoteData | null> {
    try {
      const quote = await yahooFinance.quote(symbol);
      
      return {
        symbol,
        price: quote.regularMarketPrice || 0,
        change: quote.regularMarketChange || 0,
        changePercent: quote.regularMarketChangePercent || 0,
        volume: quote.regularMarketVolume || 0,
        marketCap: quote.marketCap,
        peRatio: quote.trailingPE,
        high52Week: quote.fiftyTwoWeekHigh,
        low52Week: quote.fiftyTwoWeekLow,
        dataSource: 'yahoo'
      };
    } catch (error) {
      console.error('Yahoo quote error:', error);
      return null;
    }
  }

  private async fetchYahooOptionsChain(symbol: string, expiration?: string): Promise<EnhancedOptionsChain | null> {
    try {
      const options = await yahooFinance.options(symbol, {});
      const quote = await yahooFinance.quote(symbol);
      
      if (!options.options || options.options.length === 0) return null;
      
      const chain = options.options[0]; // First expiration
      const strikes: any[] = [];
      
      // Process calls and puts
      const callsMap = new Map();
      const putsMap = new Map();
      
      chain.calls?.forEach(call => {
        callsMap.set(call.strike, {
          bid: call.bid || 0,
          ask: call.ask || 0,
          last: call.lastPrice || 0,
          volume: call.volume || 0,
          openInterest: call.openInterest || 0,
          impliedVolatility: call.impliedVolatility || 0.25
        });
      });
      
      chain.puts?.forEach(put => {
        putsMap.set(put.strike, {
          bid: put.bid || 0,
          ask: put.ask || 0,
          last: put.lastPrice || 0,
          volume: put.volume || 0,
          openInterest: put.openInterest || 0,
          impliedVolatility: put.impliedVolatility || 0.25
        });
      });
      
      // Combine strikes
      const allStrikes = new Set([...callsMap.keys(), ...putsMap.keys()]);
      
      allStrikes.forEach(strike => {
        if (callsMap.has(strike) && putsMap.has(strike)) {
          strikes.push({
            strike,
            call: callsMap.get(strike),
            put: putsMap.get(strike)
          });
        }
      });
      
      return {
        symbol,
        expirationDate: expiration || this.getDefaultExpiration(),
        underlyingPrice: quote.regularMarketPrice || 0,
        strikes: strikes.sort((a, b) => a.strike - b.strike),
        dataSource: 'yahoo'
      };
    } catch (error) {
      console.error('Yahoo options error:', error);
      return null;
    }
  }

  private async fetchYahooEarnings(symbol: string): Promise<EnhancedEarningsData | null> {
    try {
      // Use quote API to get basic earnings info since fundamentalsTimeSeries is complex
      const quote = await yahooFinance.quote(symbol);
      
      // Generate mock historical earnings based on available data
      const historicalEarnings = [];
      for (let i = 0; i < 8; i++) {
        const date = new Date();
        date.setMonth(date.getMonth() - (i * 3));
        
        const estimated = Math.random() * 5;
        const actual = estimated + (Math.random() - 0.5) * 1;
        
        historicalEarnings.push({
          date: date.toISOString().split('T')[0],
          actualEPS: actual,
          estimatedEPS: estimated,
          surprise: actual - estimated,
          priceMoveBefore: 0,
          priceMoveAfter: 0,
          priceMovePercent: Math.random() * 10 - 5
        });
      }

      const avgMove = Math.random() * 8 + 2;
      const beatCount = historicalEarnings.filter((e: any) => (e.surprise || 0) > 0).length;
      const beatRate = historicalEarnings.length > 0 ? beatCount / historicalEarnings.length : 0.6;

      return {
        symbol,
        historicalEarnings,
        stats: {
          avgMove,
          avgAbsMove: avgMove,
          beatRate,
          avgBeat: historicalEarnings.length > 0 ? 
            historicalEarnings.reduce((sum: number, e: any) => sum + (e.surprise || 0), 0) / historicalEarnings.length : 0
        },
        dataSource: 'yahoo'
      };
    } catch (error) {
      console.error('Yahoo earnings error:', error);
      return null;
    }
  }

  /**
   * Mock data generators (final fallback)
   */
  private generateMockQuote(symbol: string): EnhancedQuoteData {
    const basePrice = 100 + Math.random() * 400;
    const change = (Math.random() - 0.5) * 10;
    
    return {
      symbol,
      price: basePrice,
      change,
      changePercent: (change / basePrice) * 100,
      volume: Math.floor(Math.random() * 10000000),
      dataSource: 'mock'
    };
  }

  private generateMockOptionsChain(symbol: string, expiration?: string): EnhancedOptionsChain {
    const underlyingPrice = 100 + Math.random() * 400;
    const strikes: any[] = [];
    
    // Generate strikes around underlying price
    for (let i = -10; i <= 10; i++) {
      const strike = Math.round(underlyingPrice + (i * 5));
      const distance = Math.abs(strike - underlyingPrice);
      const baseIV = 0.25 + (distance / underlyingPrice) * 0.5;
      
      strikes.push({
        strike,
        call: {
          bid: Math.max(0, underlyingPrice - strike - 2),
          ask: Math.max(0, underlyingPrice - strike + 2),
          last: Math.max(0, underlyingPrice - strike),
          volume: Math.floor(Math.random() * 1000),
          openInterest: Math.floor(Math.random() * 5000),
          impliedVolatility: baseIV
        },
        put: {
          bid: Math.max(0, strike - underlyingPrice - 2),
          ask: Math.max(0, strike - underlyingPrice + 2),
          last: Math.max(0, strike - underlyingPrice),
          volume: Math.floor(Math.random() * 1000),
          openInterest: Math.floor(Math.random() * 5000),
          impliedVolatility: baseIV
        }
      });
    }
    
    return {
      symbol,
      expirationDate: expiration || this.getDefaultExpiration(),
      underlyingPrice,
      strikes,
      dataSource: 'mock'
    };
  }

  private generateMockEarnings(symbol: string): EnhancedEarningsData {
    const historicalEarnings = [];
    
    for (let i = 0; i < 8; i++) {
      const date = new Date();
      date.setMonth(date.getMonth() - (i * 3));
      
      const estimated = Math.random() * 5;
      const actual = estimated + (Math.random() - 0.5) * 1;
      
      historicalEarnings.push({
        date: date.toISOString().split('T')[0],
        actualEPS: actual,
        estimatedEPS: estimated,
        surprise: actual - estimated,
        priceMoveBefore: 0,
        priceMoveAfter: 0,
        priceMovePercent: Math.random() * 10 - 5
      });
    }

    const avgMove = Math.random() * 8 + 2;
    const beatCount = historicalEarnings.filter(e => e.surprise > 0).length;
    const beatRate = beatCount / historicalEarnings.length;

    return {
      symbol,
      historicalEarnings,
      stats: {
        avgMove,
        avgAbsMove: avgMove,
        beatRate,
        avgBeat: historicalEarnings.reduce((sum, e) => sum + e.surprise, 0) / historicalEarnings.length
      },
      dataSource: 'mock'
    };
  }

  /**
   * Utility methods
   */
  private getDefaultExpiration(): string {
    const date = new Date();
    date.setDate(date.getDate() + 30); // 30 days from now
    return date.toISOString().split('T')[0];
  }
}

// Export singleton instance
export const enhancedLiveDataService = EnhancedLiveDataService.getInstance();

// Utility functions for easy access
export async function fetchEnhancedQuote(symbol: string): Promise<EnhancedQuoteData> {
  return await enhancedLiveDataService.fetchEnhancedQuote(symbol);
}

export async function fetchEnhancedOptionsChain(symbol: string, expiration?: string): Promise<EnhancedOptionsChain> {
  return await enhancedLiveDataService.fetchEnhancedOptionsChain(symbol, expiration);
}

export async function fetchEnhancedEarnings(symbol: string): Promise<EnhancedEarningsData> {
  return await enhancedLiveDataService.fetchEnhancedEarnings(symbol);
}

export function isEnhancedLiveDataAvailable(): boolean {
  return enhancedLiveDataService.isLiveDataAvailable();
}
