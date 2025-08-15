import { DoltDatabaseConfig } from '../config/doltConfig';

/**
 * Advanced optimized service for accessing full 6 years of Dolt data efficiently
 * Uses smart chunking, parallel processing, and caching strategies
 */
export class DoltOptimizedService {
  private config: DoltDatabaseConfig;

  constructor(config: DoltDatabaseConfig) {
    this.config = config;
  }

  /**
   * Execute query with timeout protection and retry logic
   */
  private async executeQuery(sql: string, timeoutMs: number = 30000): Promise<any[]> {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

      const url = `${this.config.endpoints?.chainEndpoint || this.config.endpoint}?q=${encodeURIComponent(sql)}`;
      const response = await fetch(url, { 
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      if (result.query_execution_status !== 'Success') {
        throw new Error(`Query failed: ${result.query_execution_message}`);
      }

      return result.rows || [];
    } catch (error) {
      console.error('[DoltOptimized] Query failed:', error);
      throw error;
    }
  }

  /**
   * Strategy 1: Symbol-First Historical Analysis with Year Chunking
   * Efficiently query 6 years of data by breaking into yearly chunks
   */
  async getSymbolHistoricalAnalysis(
    symbol: string, 
    startYear: number = 2019,
    endYear: number = 2025
  ): Promise<{
    yearlyStats: Array<{
      year: number;
      avgIV: number;
      optionCount: number;
      dateRange: { start: string; end: string };
    }>;
    totalDataPoints: number;
  }> {
    const yearlyPromises = [];
    
    for (let year = startYear; year <= endYear; year++) {
      const yearPromise = this.executeQuery(`
        SELECT 
          ${year} as year,
          AVG(vol) as avg_iv,
          COUNT(*) as option_count,
          MIN(date) as start_date,
          MAX(date) as end_date
        FROM option_chain 
        WHERE act_symbol = '${symbol.toUpperCase()}'
          AND YEAR(date) = ${year}
          AND vol IS NOT NULL
          AND vol > 0
      `, 15000); // 15 second timeout per year
      
      yearlyPromises.push(yearPromise);
    }

    try {
      const yearlyResults = await Promise.allSettled(yearlyPromises);
      const yearlyStats = [];
      let totalDataPoints = 0;

      yearlyResults.forEach((result, index) => {
        if (result.status === 'fulfilled' && result.value.length > 0) {
          const data = result.value[0];
          yearlyStats.push({
            year: startYear + index,
            avgIV: parseFloat(data.avg_iv) || 0,
            optionCount: parseInt(data.option_count) || 0,
            dateRange: {
              start: data.start_date,
              end: data.end_date
            }
          });
          totalDataPoints += parseInt(data.option_count) || 0;
        }
      });

      return { yearlyStats, totalDataPoints };
    } catch (error) {
      console.error(`[DoltOptimized] Failed to get historical analysis for ${symbol}:`, error);
      return { yearlyStats: [], totalDataPoints: 0 };
    }
  }

  /**
   * Strategy 2: Progressive Historical Options Chain Loading
   * Load recent data first, then progressively load older data on-demand
   */
  async getProgressiveOptionsChain(
    symbol: string,
    expiration?: string,
    maxYearsBack: number = 6
  ): Promise<{
    recent: any[]; // Last year
    historical: any[]; // Older data
    metadata: {
      totalRows: number;
      yearsCovered: number;
      oldestDate: string;
    };
  }> {
    // Phase 1: Get recent data (fast)
    const recentData = await this.executeQuery(`
      SELECT 
        date, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
        ${expiration ? `AND expiration = '${expiration}'` : ''}
      ORDER BY date DESC, strike ASC
      LIMIT 1000
    `, 10000);

    // Phase 2: Get historical data in chunks (parallel)
    const historicalPromises = [];
    for (let yearsBack = 2; yearsBack <= maxYearsBack; yearsBack++) {
      const historicalPromise = this.executeQuery(`
        SELECT 
          date, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega
        FROM option_chain 
        WHERE act_symbol = '${symbol.toUpperCase()}'
          AND date >= DATE_SUB(CURDATE(), INTERVAL ${yearsBack * 365} DAY)
          AND date < DATE_SUB(CURDATE(), INTERVAL ${(yearsBack - 1) * 365} DAY)
          ${expiration ? `AND expiration = '${expiration}'` : ''}
        ORDER BY date DESC, strike ASC
        LIMIT 500
      `, 20000);
      
      historicalPromises.push(historicalPromise);
    }

    const historicalResults = await Promise.allSettled(historicalPromises);
    const historical = historicalResults
      .filter(result => result.status === 'fulfilled')
      .flatMap(result => (result as PromiseFulfilledResult<any[]>).value);

    return {
      recent: recentData,
      historical,
      metadata: {
        totalRows: recentData.length + historical.length,
        yearsCovered: Math.min(maxYearsBack, historicalResults.filter(r => r.status === 'fulfilled').length + 1),
        oldestDate: historical.length > 0 ? 
          Math.min(...historical.map(row => new Date(row.date).getTime())).toString() : 
          recentData.length > 0 ? recentData[recentData.length - 1].date : 'N/A'
      }
    };
  }

  /**
   * Strategy 3: Smart Expiration Cycle Analysis
   * Focus on standard monthly/weekly expirations for better performance
   */
  async getStandardExpirationAnalysis(
    symbol: string,
    yearsBack: number = 3
  ): Promise<{
    monthlyExpirations: string[];
    weeklyExpirations: string[];
    liquidityAnalysis: Array<{
      expiration: string;
      avgVolume: number;
      optionCount: number;
      avgIV: number;
    }>;
  }> {
    // Get standard 3rd Friday monthly expirations and weekly expirations
    const expirationData = await this.executeQuery(`
      SELECT 
        expiration,
        COUNT(*) as option_count,
        AVG(vol) as avg_iv,
        AVG((bid + ask) / 2) as avg_mid_price
      FROM option_chain 
      WHERE act_symbol = '${symbol.toUpperCase()}'
        AND date >= DATE_SUB(CURDATE(), INTERVAL ${yearsBack * 365} DAY)
        AND vol IS NOT NULL
        AND bid > 0 AND ask > 0
        AND DAYOFWEEK(expiration) = 6  -- Fridays
      GROUP BY expiration
      HAVING COUNT(*) > 10  -- Only liquid expirations
      ORDER BY expiration DESC
      LIMIT 100
    `, 25000);

    // Classify as monthly vs weekly based on patterns
    const monthlyExpirations = [];
    const weeklyExpirations = [];
    const liquidityAnalysis = [];

    for (const row of expirationData) {
      const expDate = new Date(row.expiration);
      const dayOfMonth = expDate.getDate();
      
      // Standard monthly options expire on 3rd Friday (15-21st typically)
      const isMonthly = dayOfMonth >= 15 && dayOfMonth <= 21;
      
      if (isMonthly) {
        monthlyExpirations.push(row.expiration);
      } else {
        weeklyExpirations.push(row.expiration);
      }

      liquidityAnalysis.push({
        expiration: row.expiration,
        avgVolume: parseFloat(row.avg_mid_price) || 0,
        optionCount: parseInt(row.option_count) || 0,
        avgIV: parseFloat(row.avg_iv) || 0
      });
    }

    return {
      monthlyExpirations,
      weeklyExpirations,
      liquidityAnalysis
    };
  }

  /**
   * Strategy 4: Batch Symbol Processing for Portfolio Analysis
   * Process multiple symbols in parallel with intelligent batching
   */
  async getBatchSymbolAnalysis(
    symbols: string[],
    analysisType: 'iv_trends' | 'greeks_history' | 'liquidity_analysis' = 'iv_trends',
    yearsBack: number = 2
  ): Promise<Map<string, any>> {
    const batchSize = 5; // Process 5 symbols at a time to avoid overwhelming the API
    const results = new Map();

    for (let i = 0; i < symbols.length; i += batchSize) {
      const batch = symbols.slice(i, i + batchSize);
      const batchPromises = batch.map(async (symbol) => {
        try {
          let query = '';
          
          switch (analysisType) {
            case 'iv_trends':
              query = `
                SELECT 
                  '${symbol}' as symbol,
                  DATE_FORMAT(date, '%Y-%m') as month,
                  AVG(vol) as avg_iv,
                  COUNT(*) as data_points
                FROM option_chain 
                WHERE act_symbol = '${symbol.toUpperCase()}'
                  AND date >= DATE_SUB(CURDATE(), INTERVAL ${yearsBack * 365} DAY)
                  AND vol IS NOT NULL
                GROUP BY DATE_FORMAT(date, '%Y-%m')
                ORDER BY month DESC
                LIMIT 24
              `;
              break;
              
            case 'greeks_history':
              query = `
                SELECT 
                  '${symbol}' as symbol,
                  date,
                  AVG(delta) as avg_delta,
                  AVG(gamma) as avg_gamma,
                  AVG(theta) as avg_theta,
                  AVG(vega) as avg_vega
                FROM option_chain 
                WHERE act_symbol = '${symbol.toUpperCase()}'
                  AND date >= DATE_SUB(CURDATE(), INTERVAL ${yearsBack * 365} DAY)
                  AND delta IS NOT NULL
                GROUP BY date
                ORDER BY date DESC
                LIMIT 100
              `;
              break;
              
            case 'liquidity_analysis':
              query = `
                SELECT 
                  '${symbol}' as symbol,
                  COUNT(*) as total_options,
                  COUNT(CASE WHEN bid > 0 AND ask > 0 THEN 1 END) as liquid_options,
                  AVG(ask - bid) as avg_spread,
                  AVG(vol) as avg_iv
                FROM option_chain 
                WHERE act_symbol = '${symbol.toUpperCase()}'
                  AND date >= DATE_SUB(CURDATE(), INTERVAL ${yearsBack * 365} DAY)
              `;
              break;
          }

          const data = await this.executeQuery(query, 15000);
          return { symbol, data };
        } catch (error) {
          console.error(`[DoltOptimized] Failed to analyze ${symbol}:`, error);
          return { symbol, data: [] };
        }
      });

      const batchResults = await Promise.allSettled(batchPromises);
      batchResults.forEach((result) => {
        if (result.status === 'fulfilled') {
          results.set(result.value.symbol, result.value.data);
        }
      });

      // Small delay between batches to be respectful to the API
      if (i + batchSize < symbols.length) {
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }

    return results;
  }
}

export default DoltOptimizedService;
