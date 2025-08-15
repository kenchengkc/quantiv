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
   * Execute SQL query against DoltHub API
   */
  private async executeQuery(sql: string, endpointType: 'chain' | 'iv' = 'chain'): Promise<any[]> {
    try {
      // Use specific endpoint if configured, otherwise fall back to general endpoint
      let url: string;
      if (this.config.endpoints) {
        const specificEndpoint = endpointType === 'iv' ? this.config.endpoints.ivEndpoint : this.config.endpoints.chainEndpoint;
        const encodedQuery = encodeURIComponent(sql);
        url = `${specificEndpoint}?q=${encodedQuery}`;
      } else {
        // Fallback to general endpoint construction
        const encodedQuery = encodeURIComponent(sql);
        url = `${this.config.endpoint}/${this.config.database}/${this.config.branch}?q=${encodedQuery}`;
      }
      
      console.log(`[Dolt] Executing ${endpointType} query: ${sql}`);
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
        const errorText = await response.text();
        throw new Error(`Dolt API error: ${response.status} ${response.statusText} - ${errorText}`);
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
}

export default DoltService;
