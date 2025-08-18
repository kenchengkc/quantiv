/**
 * Local Dolt Service - SQLite Implementation
 * High-performance local database queries for options and volatility data
 * Replaces DoltHub API with unlimited query capabilities
 */

import Database from 'better-sqlite3';
import path from 'path';

interface OptionsChainRow {
  id: number;
  date: string;
  act_symbol: string;
  expiration: string;
  strike: number;
  call_put: string;
  bid: number | null;
  ask: number | null;
  vol: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  open_interest: number | null;
  volume: number | null;
}

interface VolatilityRow {
  id: number;
  date: string;
  symbol: string;
  iv: number | null;
  hv: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
}

interface IVHistoryPoint {
  date: string;
  iv: number;
}

interface OptionsChainData {
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
    date?: string;
    totalOptions: number;
    avgIV: number;
  };
}

interface IVStats {
  current: number;
  rank: number;
  percentile: number;
  historical: number;
}

export class LocalDoltService {
  private db: Database.Database | null = null;
  private dbPath: string;

  constructor() {
    this.dbPath = path.join(process.cwd(), 'data', 'quantiv_options.db');
  }

  private getDatabase(): Database.Database {
    if (!this.db) {
      try {
        this.db = new Database(this.dbPath, { readonly: true });
        
        // Optimize for read performance
        this.db.pragma('journal_mode = WAL');
        this.db.pragma('synchronous = NORMAL');
        this.db.pragma('cache_size = 10000');
        this.db.pragma('temp_store = memory');
        
        console.log('[LocalDolt] Database connection established');
      } catch (error) {
        console.error('[LocalDolt] Failed to connect to database:', error);
        throw new Error(`Failed to connect to SQLite database: ${error}`);
      }
    }
    return this.db;
  }

  /**
   * Get IV history for a symbol (replaces DoltHub API method)
   */
  async getIVHistory(symbol: string, days: number = 252): Promise<IVHistoryPoint[]> {
    const db = this.getDatabase();
    
    const query = `
      SELECT date, iv
      FROM volatility_history 
      WHERE symbol = ? 
        AND iv IS NOT NULL
        AND date >= date('now', '-${days} days')
      ORDER BY date ASC
      LIMIT 1000
    `;

    try {
      const rows = db.prepare(query).all(symbol) as VolatilityRow[];
      
      return rows.map(row => ({
        date: row.date,
        iv: row.iv || 0
      }));
    } catch (error) {
      console.error(`[LocalDolt] Failed to get IV history for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get IV statistics for a symbol
   */
  async getIVStats(symbol: string): Promise<IVStats | null> {
    const db = this.getDatabase();
    
    const query = `
      SELECT 
        iv as current_iv,
        iv_rank,
        iv_percentile,
        hv as historical_vol
      FROM volatility_history 
      WHERE symbol = ?
        AND iv IS NOT NULL
      ORDER BY date DESC
      LIMIT 1
    `;

    try {
      const row = db.prepare(query).get(symbol) as VolatilityRow | undefined;
      
      if (!row) return null;

      return {
        current: row.iv || 0,
        rank: row.iv_rank || 0,
        percentile: row.iv_percentile || 0,
        historical: row.hv || 0
      };
    } catch (error) {
      console.error(`[LocalDolt] Failed to get IV stats for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Get options chain for a symbol and date
   */
  async getOptionsChain(symbol: string, date?: string): Promise<OptionsChainData> {
    const db = this.getDatabase();
    
    let query = `
      SELECT 
        date, expiration, strike, call_put, bid, ask, vol,
        delta, gamma, theta, vega
      FROM options_chain 
      WHERE act_symbol = ?
        AND vol IS NOT NULL
    `;

    const params: any[] = [symbol];

    if (date) {
      query += ' AND date = ?';
      params.push(date);
    } else {
      // Get most recent date
      query += ` AND date = (
        SELECT MAX(date) FROM options_chain WHERE act_symbol = ?
      )`;
      params.push(symbol);
    }

    query += ' ORDER BY expiration ASC, strike ASC, call_put ASC LIMIT 1000';

    try {
      const rows = db.prepare(query).all(...params) as OptionsChainRow[];
      
      const options = rows.map(row => ({
        date: row.date,
        expiration: row.expiration,
        strike: row.strike,
        call_put: row.call_put,
        bid: row.bid || 0,
        ask: row.ask || 0,
        vol: row.vol || 0,
        delta: row.delta || undefined,
        gamma: row.gamma || undefined,
        theta: row.theta || undefined,
        vega: row.vega || undefined,
      }));

      const avgIV = options.length > 0 
        ? options.reduce((sum, opt) => sum + opt.vol, 0) / options.length 
        : 0;

      return {
        options,
        metadata: {
          symbol: symbol.toUpperCase(),
          date: date || (options.length > 0 ? options[0].date : undefined),
          totalOptions: options.length,
          avgIV
        }
      };
    } catch (error) {
      console.error(`[LocalDolt] Failed to get options chain for ${symbol}:`, error);
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
   * Get available dates for a symbol
   */
  async getAvailableDates(symbol: string, limit: number = 10): Promise<string[]> {
    const db = this.getDatabase();
    
    const query = `
      SELECT DISTINCT date
      FROM options_chain 
      WHERE act_symbol = ?
      ORDER BY date DESC
      LIMIT ?
    `;

    try {
      const rows = db.prepare(query).all(symbol, limit) as { date: string }[];
      return rows.map(row => row.date);
    } catch (error) {
      console.error(`[LocalDolt] Failed to get available dates for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Get summary statistics for a symbol on a specific date
   */
  async getDailySummary(symbol: string, date: string): Promise<{
    totalOptions: number;
    avgIV: number;
    avgDelta: number;
    liquidOptions: number;
    avgSpread: number;
  }> {
    const db = this.getDatabase();
    
    const query = `
      SELECT 
        COUNT(*) as total_options,
        AVG(vol) as avg_iv,
        AVG(delta) as avg_delta,
        COUNT(CASE WHEN bid > 0 AND ask > 0 THEN 1 END) as liquid_options,
        AVG(ask - bid) as avg_spread
      FROM options_chain 
      WHERE act_symbol = ?
        AND date = ?
        AND vol IS NOT NULL
    `;

    try {
      const row = db.prepare(query).get(symbol, date) as any;
      
      return {
        totalOptions: row?.total_options || 0,
        avgIV: row?.avg_iv || 0,
        avgDelta: row?.avg_delta || 0,
        liquidOptions: row?.liquid_options || 0,
        avgSpread: row?.avg_spread || 0
      };
    } catch (error) {
      console.error(`[LocalDolt] Failed to get daily summary for ${symbol}:`, error);
      return {
        totalOptions: 0,
        avgIV: 0,
        avgDelta: 0,
        liquidOptions: 0,
        avgSpread: 0
      };
    }
  }

  /**
   * Get available symbols in the database
   */
  async getAvailableSymbols(limit: number = 100): Promise<string[]> {
    const db = this.getDatabase();
    
    const query = `
      SELECT symbol
      FROM symbols_metadata 
      ORDER BY total_options DESC
      LIMIT ?
    `;

    try {
      const rows = db.prepare(query).all(limit) as { symbol: string }[];
      return rows.map(row => row.symbol);
    } catch (error) {
      console.error('[LocalDolt] Failed to get available symbols:', error);
      return [];
    }
  }

  /**
   * Get ATM options for expected move calculations
   */
  async getATMOptions(symbol: string, date?: string): Promise<{
    calls: OptionsChainRow[];
    puts: OptionsChainRow[];
    atmStrike: number;
  }> {
    const db = this.getDatabase();
    
    let query = `
      SELECT *
      FROM options_chain 
      WHERE act_symbol = ?
        AND vol IS NOT NULL
        AND bid > 0 AND ask > 0
    `;

    const params: any[] = [symbol];

    if (date) {
      query += ' AND date = ?';
      params.push(date);
    } else {
      query += ` AND date = (
        SELECT MAX(date) FROM options_chain WHERE act_symbol = ?
      )`;
      params.push(symbol);
    }

    query += ' ORDER BY ABS(strike - (SELECT AVG(strike) FROM options_chain WHERE act_symbol = ? AND date = ?)) ASC';
    params.push(symbol, date || 'latest');

    try {
      const rows = db.prepare(query).all(...params) as OptionsChainRow[];
      
      const calls = rows.filter(row => row.call_put.toLowerCase().startsWith('c'));
      const puts = rows.filter(row => row.call_put.toLowerCase().startsWith('p'));
      
      // Estimate ATM strike from available data
      const atmStrike = rows.length > 0 
        ? rows.reduce((sum, row) => sum + row.strike, 0) / rows.length
        : 0;

      return { calls, puts, atmStrike };
    } catch (error) {
      console.error(`[LocalDolt] Failed to get ATM options for ${symbol}:`, error);
      return { calls: [], puts: [], atmStrike: 0 };
    }
  }

  /**
   * Close database connection
   */
  close(): void {
    if (this.db) {
      this.db.close();
      this.db = null;
      console.log('[LocalDolt] Database connection closed');
    }
  }
}

// Export singleton instance
export const localDoltService = new LocalDoltService();
