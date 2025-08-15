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
}

class DoltService {
  private static instance: DoltService;
  private config: DoltConfig;

  constructor(config: DoltConfig) {
    this.config = {
      branch: 'main',
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
   * Execute SQL query against DoltHub API
   */
  private async executeQuery(sql: string): Promise<any[]> {
    try {
      // DoltHub API format: https://www.dolthub.com/api/v1alpha1/{owner}/{repo}/{branch}?q={sql}
      const encodedQuery = encodeURIComponent(sql);
      const url = `${this.config.endpoint}/${this.config.database}/${this.config.branch}?q=${encodedQuery}`;
      
      console.log(`[Dolt] Executing query: ${sql}`);
      console.log(`[Dolt] URL: ${url}`);

      const headers: Record<string, string> = {
        'Accept': 'application/json',
      };

      if (this.config.apiKey) {
        headers['Authorization'] = `Bearer ${this.config.apiKey}`;
      }

      const response = await fetch(url, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Dolt API error: ${response.status} ${response.statusText}`);
      }

      const result = await response.json();
      
      if (result.query_execution_status !== 'Success') {
        throw new Error(`Dolt query failed: ${result.query_execution_message}`);
      }

      console.log(`[Dolt] Query successful, returned ${result.rows?.length || 0} rows`);
      return result.rows || [];
    } catch (error) {
      console.error('[Dolt] Query execution failed:', error);
      throw error;
    }
  }

  /**
   * Get historical IV data for sparkline visualization from option_chain table
   */
  async getIVHistory(symbol: string, days: number = 252): Promise<Array<{
    date: string;
    iv: number;
  }>> {
    const sql = `
      SELECT 
        date,
        AVG(vol) as avg_iv
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL ${days} DAY)
        AND vol IS NOT NULL
      GROUP BY date
      ORDER BY date ASC
    `;

    try {
      const rows = await this.executeQuery(sql);
      return rows.map(row => ({
        date: row.date,
        iv: parseFloat(row.avg_iv) * 100, // Convert to percentage
      }));
    } catch (error) {
      console.error(`[Dolt] Failed to fetch IV history for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get current IV statistics for a symbol
   */
  async getCurrentIVStats(symbol: string): Promise<{
    rank: number;
    percentile: number;
    current: number;
    high52Week: number;
    low52Week: number;
  } | null> {
    const sql = `
      SELECT 
        iv_rank,
        iv_percentile,
        current_iv,
        high_52w,
        low_52w
      FROM iv_history 
      WHERE symbol = '${symbol.toUpperCase()}'
      ORDER BY date DESC 
      LIMIT 1
    `;

    try {
      const rows = await this.executeQuery(sql);
      if (rows.length === 0) return null;

      const row = rows[0];
      return {
        rank: row.iv_rank || 50,
        percentile: row.iv_percentile || 50,
        current: row.current_iv || 25,
        high52Week: row.high_52w || 40,
        low52Week: row.low_52w || 15,
      };
    } catch (error) {
      console.error(`[Dolt] Failed to fetch current IV stats for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get historical options chain data for expected move calculations
   */
  async getHistoricalOptionsChain(
    symbol: string, 
    date: string,
    expiration?: string
  ): Promise<DoltOptionsChain[]> {
    let sql = `
      SELECT 
        symbol,
        date,
        expiration,
        strike,
        option_type,
        bid,
        ask,
        last,
        volume,
        open_interest,
        implied_volatility,
        delta,
        gamma,
        theta,
        vega,
        underlying_price
      FROM options_chain 
      WHERE symbol = '${symbol.toUpperCase()}'
        AND date = '${date}'
    `;

    if (expiration) {
      sql += ` AND expiration = '${expiration}'`;
    }

    sql += ` ORDER BY strike ASC, option_type ASC`;

    try {
      const rows = await this.executeQuery(sql);
      return rows.map(row => DoltOptionsChainSchema.parse(row));
    } catch (error) {
      console.error(`[Dolt] Failed to fetch options chain for ${symbol} on ${date}:`, error);
      return [];
    }
  }

  /**
   * Get available expiration dates for a symbol
   */
  async getAvailableExpirations(symbol: string, fromDate?: string): Promise<string[]> {
    let sql = `
      SELECT DISTINCT expiration
      FROM options_chain 
      WHERE symbol = '${symbol.toUpperCase()}'
    `;

    if (fromDate) {
      sql += ` AND date >= '${fromDate}'`;
    }

    sql += ` ORDER BY expiration ASC`;

    try {
      const rows = await this.executeQuery(sql);
      return rows.map(row => row.expiration);
    } catch (error) {
      console.error(`[Dolt] Failed to fetch expirations for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get symbols available in the database
   */
  async getAvailableSymbols(): Promise<string[]> {
    const sql = `
      SELECT DISTINCT symbol
      FROM options_chain 
      ORDER BY symbol ASC
    `;

    try {
      const rows = await this.executeQuery(sql);
      return rows.map(row => row.symbol);
    } catch (error) {
      console.error('[Dolt] Failed to fetch available symbols:', error);
      return [];
    }
  }

  /**
   * Get the most recent data date for a symbol
   */
  async getLatestDataDate(symbol: string): Promise<string | null> {
    const sql = `
      SELECT MAX(date) as latest_date
      FROM options_chain 
      WHERE symbol = '${symbol.toUpperCase()}'
    `;

    try {
      const rows = await this.executeQuery(sql);
      return rows[0]?.latest_date || null;
    } catch (error) {
      console.error(`[Dolt] Failed to fetch latest date for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Calculate historical expected moves from options data
   */
  async getHistoricalExpectedMoves(
    symbol: string, 
    days: number = 30
  ): Promise<Array<{
    date: string;
    expectedMove: number;
    actualMove?: number;
    accuracy?: number;
  }>> {
    const sql = `
      SELECT 
        date,
        expiration,
        strike,
        option_type,
        last,
        underlying_price,
        implied_volatility
      FROM options_chain 
      WHERE symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL ${days} DAY)
        AND ABS(strike - underlying_price) <= underlying_price * 0.05
      ORDER BY date ASC, ABS(strike - underlying_price) ASC
    `;

    try {
      const rows = await this.executeQuery(sql);
      
      // Group by date and calculate straddle prices
      const movesByDate = new Map<string, {
        date: string;
        expectedMove: number;
        underlyingPrice: number;
      }>();

      for (const row of rows) {
        const date = row.date;
        if (!movesByDate.has(date)) {
          // Find ATM straddle for this date
          const atmStrike = row.strike;
          const callPrice = row.option_type === 'call' ? (row.last || 0) : 0;
          const putPrice = row.option_type === 'put' ? (row.last || 0) : 0;
          
          // This is simplified - in practice you'd want to match call/put pairs
          const straddlePrice = callPrice + putPrice;
          const expectedMove = straddlePrice * 0.85; // Approximate expected move
          
          movesByDate.set(date, {
            date,
            expectedMove,
            underlyingPrice: row.underlying_price || 0,
          });
        }
      }

      return Array.from(movesByDate.values());
    } catch (error) {
      console.error(`[Dolt] Failed to calculate historical expected moves for ${symbol}:`, error);
      return [];
    }
  }
}

export default DoltService;
