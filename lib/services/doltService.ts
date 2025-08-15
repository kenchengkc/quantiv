/**
 * Dolt Database Service
 * Provides access to historical options chain and IV data for S&P 500 stocks
 */

import { z } from 'zod';

// Dolt API response schemas based on discovered structure
const DoltOptionsChainSchema = z.object({
  date: z.string(), // Date of the options data
  act_symbol: z.string(), // Stock symbol
  expiration: z.string(), // Option expiration date
  strike: z.string().transform(val => parseFloat(val)), // Strike price (comes as string)
  call_put: z.enum(['Call', 'Put']), // "Call" or "Put"
  bid: z.string().transform(val => parseFloat(val)).nullable(), // Bid price
  ask: z.string().transform(val => parseFloat(val)).nullable(), // Ask price
  vol: z.string().transform(val => parseFloat(val)).nullable(), // Implied volatility
  delta: z.string().transform(val => parseFloat(val)).nullable(), // Delta
  gamma: z.string().transform(val => parseFloat(val)).nullable(), // Gamma
  theta: z.string().transform(val => parseFloat(val)).nullable(), // Theta
  vega: z.string().transform(val => parseFloat(val)).nullable(), // Vega
  rho: z.string().transform(val => parseFloat(val)).nullable(), // Rho
});

const DoltIVHistorySchema = z.object({
  symbol: z.string(),
  date: z.string(),
  iv_rank: z.number().nullable(),
  iv_percentile: z.number().nullable(),
  current_iv: z.number().nullable(),
  high_52w: z.number().nullable(),
  low_52w: z.number().nullable(),
  avg_iv: z.number().nullable(),
});

export type DoltOptionsChain = z.infer<typeof DoltOptionsChainSchema>;
export type DoltIVHistory = z.infer<typeof DoltIVHistorySchema>;

export interface DoltConfig {
  endpoint: string;
  database: string;
  branch?: string;
  apiKey?: string;
  endpoints?: {
    chainEndpoint: string;
    ivEndpoint: string;
  };
}

class DoltService {
  private static instance: DoltService;
  private config: DoltConfig;

  constructor(config: DoltConfig) {
    this.config = {
      branch: 'master',
      ...config,
    };
  }

  static getInstance(config?: DoltConfig): DoltService {
    if (!DoltService.instance) {
      if (!config) {
        throw new Error('DoltService requires configuration on first initialization');
      }
      DoltService.instance = new DoltService(config);
    }
    return DoltService.instance;
  }

  /**
   * Execute a SQL query against the Dolt database with timeout protection
   */
  private async executeQuery(
    sql: string, 
    endpointType: 'chain' | 'iv' = 'chain',
    timeoutMs: number = 30000
  ): Promise<any[]> {
    try {
      let url: string;
      if (this.config.endpoints) {
        const specificEndpoint = endpointType === 'iv' ? this.config.endpoints.ivEndpoint : this.config.endpoints.chainEndpoint;
        const encodedQuery = encodeURIComponent(sql);
        url = `${specificEndpoint}?q=${encodedQuery}`;
      } else {
        const encodedQuery = encodeURIComponent(sql);
        url = `${this.config.endpoint}/${this.config.database}/${this.config.branch}?q=${encodedQuery}`;
      }

      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      if (this.config.apiKey) {
        headers['Authorization'] = `Bearer ${this.config.apiKey}`;
      }

      // Add timeout protection for large queries
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

      const response = await fetch(url, {
        method: 'GET',
        headers,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      if (result.query_execution_status !== 'Success') {
        throw new Error(`Dolt query failed: ${result.query_execution_message}`);
      }

      return result.rows || [];
    } catch (error) {
      console.error('[Dolt] Query execution failed:', error);
      throw error;
    }
  }

  /**
   * Get historical IV data for sparkline visualization from volatility_history table
   */
  async getIVHistory(symbol: string, days: number = 252): Promise<Array<{
    date: string;
    iv: number;
  }>> {
    const sql = `
      SELECT 
        date,
        iv_current as iv
      FROM volatility_history 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL ${days} DAY)
        AND iv_current IS NOT NULL
      ORDER BY date ASC
    `;

    try {
      const rows = await this.executeQuery(sql, 'iv');
      return rows.map(row => ({
        date: row.date,
        iv: parseFloat(row.iv) || 0,
      }));
    } catch (error) {
      console.error(`[Dolt] Failed to fetch IV history for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get current IV statistics for a symbol from volatility_history table
   */
  async getCurrentIVStats(symbol: string): Promise<{
    rank: number;
    percentile: number;
    current: number;
    high52Week: number;
    low52Week: number;
  } | null> {
    // Get latest IV stats from volatility_history table
    const sql = `
      SELECT 
        iv_current,
        iv_year_high,
        iv_year_low,
        hv_current
      FROM volatility_history 
      WHERE act_symbol = '${symbol.toUpperCase()}'
      ORDER BY date DESC 
      LIMIT 1
    `;

    try {
      const rows = await this.executeQuery(sql, 'iv');
      if (rows.length === 0 || !rows[0].iv_current) {
        console.log(`[Dolt] No IV stats found for ${symbol}`);
        return null;
      }

      const row = rows[0];
      const current = parseFloat(row.iv_current) || 0;
      const high = parseFloat(row.iv_year_high) || 0;
      const low = parseFloat(row.iv_year_low) || 0;
      
      // Calculate rank and percentile from the pre-calculated year high/low
      const range = high - low;
      const rank = range > 0 ? ((current - low) / range) * 100 : 50;
      const percentile = rank;

      return {
        rank: Math.round(rank),
        percentile: Math.round(percentile),
        current,
        high52Week: high,
        low52Week: low,
      };
    } catch (error) {
      console.error(`[Dolt] Failed to fetch current IV stats for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get available symbols in the database from volatility_history table
   */
  async getAvailableSymbols(): Promise<string[]> {
    const sql = `
      SELECT DISTINCT act_symbol
      FROM volatility_history 
      ORDER BY act_symbol ASC
    `;

    try {
      const rows = await this.executeQuery(sql, 'iv');
      return rows.map(row => row.act_symbol);
    } catch (error) {
      console.error('[Dolt] Failed to fetch available symbols:', error);
      return [];
    }
  }

  /**
   * Get the most recent data date for a symbol from volatility_history table
   */
  async getLatestDataDate(symbol: string): Promise<string | null> {
    const sql = `
      SELECT MAX(date) as latest_date
      FROM volatility_history 
      WHERE act_symbol = '${symbol.toUpperCase()}'
    `;

    try {
      const rows = await this.executeQuery(sql, 'iv');
      return rows[0]?.latest_date || null;
    } catch (error) {
      console.error(`[Dolt] Failed to fetch latest date for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get historical options chain data for a symbol (limited to past year for performance)
   */
  async getHistoricalOptionsChain(symbol: string, expiration?: string): Promise<any[]> {
    let sql = `
      SELECT 
        date,
        expiration,
        strike,
        call_put,
        bid,
        ask,
        vol,
        delta,
        gamma,
        theta,
        vega
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
    `;

    if (expiration) {
      sql += ` AND expiration = '${expiration}'`;
    }

    sql += ` ORDER BY date DESC, strike ASC LIMIT 1000`;

    try {
      const rows = await this.executeQuery(sql);
      return rows;
    } catch (error) {
      console.error(`[Dolt] Failed to fetch historical options chain for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get available expiration dates for a symbol (limited to past year for performance)
   */
  async getExpirations(symbol: string): Promise<string[]> {
    const sql = `
      SELECT DISTINCT expiration
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
        AND expiration >= CURDATE()
      ORDER BY expiration ASC
      LIMIT 50
    `;

    try {
      const rows = await this.executeQuery(sql);
      return rows.map(row => row.expiration);
    } catch (error) {
      console.error(`[Dolt] Failed to fetch expirations for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get historical expected moves for a symbol (limited to past year for performance)
   */
  async getHistoricalExpectedMoves(symbol: string, days: number = 30): Promise<Array<{
    date: string;
    expectedMove: number;
    impliedVolatility: number;
  }>> {
    // Calculate expected moves from historical options data (past year only)
    const sql = `
      SELECT 
        date,
        AVG(vol) as avg_iv,
        COUNT(*) as option_count
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
        AND date >= DATE_SUB(CURDATE(), INTERVAL ${days} DAY)
        AND vol IS NOT NULL
        AND vol > 0
      GROUP BY date
      ORDER BY date DESC
      LIMIT 100
    `;

    try {
      const rows = await this.executeQuery(sql);
      return rows.map(row => {
        const iv = parseFloat(row.avg_iv) || 0;
        // Simple expected move calculation: IV * sqrt(days/365) * price
        // We'll use a normalized expected move since we don't have price here
        const expectedMove = iv * Math.sqrt(1/365) * 100; // Daily expected move as percentage
        
        return {
          date: row.date,
          expectedMove,
          impliedVolatility: iv,
        };
      });
    } catch (error) {
      console.error(`[Dolt] Failed to fetch historical expected moves for ${symbol}:`, error);
      return [];
    }
  }

  // =============================================================================
  // ULTRA-OPTIMIZED METHODS - Single-day focused queries for DoltHub API limits
  // Based on testing: Only single-day queries with specific symbols work reliably
  // These methods work within DoltHub's strict public API constraints
  // =============================================================================

  /**
   * ULTRA-OPTIMIZED 1: Single-Day Options Chain
   * Gets options chain for a specific symbol on a specific date (DoltHub API safe)
   */
  async getSingleDayOptionsChain(
    symbol: string,
    date: string,
    expiration?: string
  ): Promise<{
    options: Array<{
      date: string;
      expiration: string;
      strike: number;
      call_put: string;
      bid: number;
      ask: number;
      vol: number;
      delta?: number;
      gamma?: number;
      theta?: number;
      vega?: number;
    }>;
    metadata: {
      symbol: string;
      date: string;
      totalOptions: number;
      avgIV: number;
    };
  }> {
    let sql = `
      SELECT 
        date, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date = '${date}'
    `;

    if (expiration) {
      sql += ` AND expiration = '${expiration}'`;
    }

    sql += ` ORDER BY strike ASC, call_put ASC LIMIT 500`;

    try {
      const rows = await this.executeQuery(sql, 'chain', 8000);
      
      const options = rows.map(row => ({
        date: row.date,
        expiration: row.expiration,
        strike: parseFloat(row.strike) || 0,
        call_put: row.call_put,
        bid: parseFloat(row.bid) || 0,
        ask: parseFloat(row.ask) || 0,
        vol: parseFloat(row.vol) || 0,
        delta: row.delta ? parseFloat(row.delta) : undefined,
        gamma: row.gamma ? parseFloat(row.gamma) : undefined,
        theta: row.theta ? parseFloat(row.theta) : undefined,
        vega: row.vega ? parseFloat(row.vega) : undefined,
      }));

      const avgIV = options.length > 0 
        ? options.reduce((sum, opt) => sum + opt.vol, 0) / options.length 
        : 0;

      return {
        options,
        metadata: {
          symbol: symbol.toUpperCase(),
          date,
          totalOptions: options.length,
          avgIV
        }
      };
    } catch (error) {
      console.error(`[Dolt] Failed to get single-day options chain for ${symbol} on ${date}:`, error);
      return {
        options: [],
        metadata: {
          symbol: symbol.toUpperCase(),
          date,
          totalOptions: 0,
          avgIV: 0
        }
      };
    }
  }

  /**
   * ULTRA-OPTIMIZED 2: Get Available Dates for Symbol
   * Gets recent trading dates where options data exists for a symbol (DoltHub API safe)
   */
  async getAvailableDatesForSymbol(
    symbol: string,
    limit: number = 10
  ): Promise<{
    dates: string[];
    metadata: {
      symbol: string;
      totalDates: number;
      latestDate: string;
    };
  }> {
    const sql = `
      SELECT DISTINCT date
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
      ORDER BY date DESC
      LIMIT ${Math.min(limit, 20)}
    `;

    try {
      const rows = await this.executeQuery(sql, 'chain', 5000);
      const dates = rows.map(row => row.date);

      return {
        dates,
        metadata: {
          symbol: symbol.toUpperCase(),
          totalDates: dates.length,
          latestDate: dates.length > 0 ? dates[0] : ''
        }
      };
    } catch (error) {
      console.error(`[Dolt] Failed to get available dates for ${symbol}:`, error);
      return {
        dates: [],
        metadata: {
          symbol: symbol.toUpperCase(),
          totalDates: 0,
          latestDate: ''
        }
      };
    }
  }

  /**
   * ULTRA-OPTIMIZED 3: Get Single-Day Expirations
   * Gets available expirations for a symbol on a specific date (DoltHub API safe)
   */
  async getSingleDayExpirations(
    symbol: string,
    date: string
  ): Promise<{
    expirations: Array<{
      expiration: string;
      optionCount: number;
      avgIV: number;
      isMonthly: boolean;
    }>;
    metadata: {
      symbol: string;
      date: string;
      totalExpirations: number;
    };
  }> {
    const sql = `
      SELECT 
        expiration,
        COUNT(*) as option_count,
        AVG(vol) as avg_iv
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date = '${date}'
        AND vol IS NOT NULL
      GROUP BY expiration
      ORDER BY expiration ASC
      LIMIT 50
    `;

    try {
      const rows = await this.executeQuery(sql, 'chain', 8000);
      
      const expirations = rows.map(row => {
        const expDate = new Date(row.expiration);
        const dayOfMonth = expDate.getDate();
        const isMonthly = dayOfMonth >= 15 && dayOfMonth <= 21; // 3rd Friday pattern
        
        return {
          expiration: row.expiration,
          optionCount: parseInt(row.option_count) || 0,
          avgIV: parseFloat(row.avg_iv) || 0,
          isMonthly
        };
      });

      return {
        expirations,
        metadata: {
          symbol: symbol.toUpperCase(),
          date,
          totalExpirations: expirations.length
        }
      };
    } catch (error) {
      console.error(`[Dolt] Failed to get expirations for ${symbol} on ${date}:`, error);
      return {
        expirations: [],
        metadata: {
          symbol: symbol.toUpperCase(),
          date,
          totalExpirations: 0
        }
      };
    }
  }

  /**
   * ULTRA-OPTIMIZED 4: Get Single-Day Summary Stats
   * Gets summary statistics for a symbol on a specific date (DoltHub API safe)
   */
  async getSingleDaySummaryStats(
    symbol: string,
    date: string
  ): Promise<{
    summary: {
      totalOptions: number;
      avgIV: number;
      avgDelta: number;
      avgGamma: number;
      liquidOptions: number;
      avgSpread: number;
    };
    metadata: {
      symbol: string;
      date: string;
      hasData: boolean;
    };
  }> {
    const sql = `
      SELECT 
        COUNT(*) as total_options,
        AVG(vol) as avg_iv,
        AVG(delta) as avg_delta,
        AVG(gamma) as avg_gamma,
        COUNT(CASE WHEN bid > 0 AND ask > 0 THEN 1 END) as liquid_options,
        AVG(ask - bid) as avg_spread
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date = '${date}'
        AND vol IS NOT NULL
    `;

    try {
      const rows = await this.executeQuery(sql, 'chain', 5000);
      
      if (rows.length === 0) {
        return {
          summary: {
            totalOptions: 0,
            avgIV: 0,
            avgDelta: 0,
            avgGamma: 0,
            liquidOptions: 0,
            avgSpread: 0
          },
          metadata: {
            symbol: symbol.toUpperCase(),
            date,
            hasData: false
          }
        };
      }

      const row = rows[0];
      return {
        summary: {
          totalOptions: parseInt(row.total_options) || 0,
          avgIV: parseFloat(row.avg_iv) || 0,
          avgDelta: parseFloat(row.avg_delta) || 0,
          avgGamma: parseFloat(row.avg_gamma) || 0,
          liquidOptions: parseInt(row.liquid_options) || 0,
          avgSpread: parseFloat(row.avg_spread) || 0
        },
        metadata: {
          symbol: symbol.toUpperCase(),
          date,
          hasData: true
        }
      };
    } catch (error) {
      console.error(`[Dolt] Failed to get summary stats for ${symbol} on ${date}:`, error);
      return {
        summary: {
          totalOptions: 0,
          avgIV: 0,
          avgDelta: 0,
          avgGamma: 0,
          liquidOptions: 0,
          avgSpread: 0
        },
        metadata: {
          symbol: symbol.toUpperCase(),
          date,
          hasData: false
        }
      };
    }
  }
}

export default DoltService;
