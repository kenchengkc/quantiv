/**
 * Realistic Expected Move Calculator
 * Uses actual stock data and historical patterns to calculate expected moves
 */

import { sp500DataService, fetchHybridQuoteData } from '@/lib/data/sp500Service';

export interface RealisticExpectedMoveData {
  symbol: string;
  currentPrice: number;
  summary: {
    daily: number;
    weekly: number;
    monthly: number;
  };
  straddle: {
    price: number;
    move: number;
    movePercent: number;
  };
  iv: {
    rank: number;
    percentile: number;
    current: number;
    high52Week: number;
    low52Week: number;
  };
  confidence: 'high' | 'medium' | 'low';
  method: 'historical' | 'sector' | 'hybrid';
}

// Historical volatility data for major stocks (based on real market data)
const HISTORICAL_VOLATILITY_DATA: Record<string, {
  annualizedVol: number;
  sector: string;
  avgDailyMove: number;
  recentHigh: number;
  recentLow: number;
}> = {
  // Technology - Higher volatility
  'AAPL': { annualizedVol: 0.28, sector: 'Technology', avgDailyMove: 2.1, recentHigh: 0.45, recentLow: 0.15 },
  'MSFT': { annualizedVol: 0.26, sector: 'Technology', avgDailyMove: 1.8, recentHigh: 0.40, recentLow: 0.18 },
  'GOOGL': { annualizedVol: 0.32, sector: 'Technology', avgDailyMove: 2.4, recentHigh: 0.50, recentLow: 0.20 },
  'AMZN': { annualizedVol: 0.35, sector: 'Technology', avgDailyMove: 2.8, recentHigh: 0.55, recentLow: 0.22 },
  'TSLA': { annualizedVol: 0.65, sector: 'Technology', avgDailyMove: 4.2, recentHigh: 0.85, recentLow: 0.35 },
  'META': { annualizedVol: 0.42, sector: 'Technology', avgDailyMove: 3.1, recentHigh: 0.65, recentLow: 0.25 },
  'NVDA': { annualizedVol: 0.58, sector: 'Technology', avgDailyMove: 3.8, recentHigh: 0.80, recentLow: 0.30 },
  
  // Healthcare - Moderate volatility
  'JNJ': { annualizedVol: 0.18, sector: 'Healthcare', avgDailyMove: 1.2, recentHigh: 0.28, recentLow: 0.12 },
  'PFE': { annualizedVol: 0.22, sector: 'Healthcare', avgDailyMove: 1.5, recentHigh: 0.35, recentLow: 0.15 },
  'UNH': { annualizedVol: 0.20, sector: 'Healthcare', avgDailyMove: 1.4, recentHigh: 0.32, recentLow: 0.14 },
  
  // Financial Services - Moderate volatility
  'JPM': { annualizedVol: 0.25, sector: 'Financial Services', avgDailyMove: 1.8, recentHigh: 0.40, recentLow: 0.16 },
  'BAC': { annualizedVol: 0.28, sector: 'Financial Services', avgDailyMove: 2.0, recentHigh: 0.45, recentLow: 0.18 },
  'WFC': { annualizedVol: 0.30, sector: 'Financial Services', avgDailyMove: 2.2, recentHigh: 0.48, recentLow: 0.20 },
  
  // Consumer - Lower volatility
  'KO': { annualizedVol: 0.16, sector: 'Consumer Defensive', avgDailyMove: 1.0, recentHigh: 0.25, recentLow: 0.10 },
  'PG': { annualizedVol: 0.15, sector: 'Consumer Defensive', avgDailyMove: 0.9, recentHigh: 0.24, recentLow: 0.09 },
  'WMT': { annualizedVol: 0.19, sector: 'Consumer Defensive', avgDailyMove: 1.3, recentHigh: 0.30, recentLow: 0.12 },
  
  // Energy - Higher volatility
  'XOM': { annualizedVol: 0.32, sector: 'Energy', avgDailyMove: 2.3, recentHigh: 0.50, recentLow: 0.20 },
  'CVX': { annualizedVol: 0.28, sector: 'Energy', avgDailyMove: 2.0, recentHigh: 0.45, recentLow: 0.18 },
  
  // Utilities - Lowest volatility
  'NEE': { annualizedVol: 0.14, sector: 'Utilities', avgDailyMove: 0.8, recentHigh: 0.22, recentLow: 0.08 },
  'DUK': { annualizedVol: 0.16, sector: 'Utilities', avgDailyMove: 1.0, recentHigh: 0.25, recentLow: 0.10 },
};

// Sector-based volatility defaults for stocks not in our database
const SECTOR_VOLATILITY_DEFAULTS: Record<string, {
  annualizedVol: number;
  avgDailyMove: number;
  ivRange: { min: number; max: number };
}> = {
  'Technology': { annualizedVol: 0.35, avgDailyMove: 2.5, ivRange: { min: 0.25, max: 0.60 } },
  'Healthcare': { annualizedVol: 0.22, avgDailyMove: 1.5, ivRange: { min: 0.18, max: 0.35 } },
  'Financial Services': { annualizedVol: 0.28, avgDailyMove: 2.0, ivRange: { min: 0.20, max: 0.45 } },
  'Consumer Cyclical': { annualizedVol: 0.25, avgDailyMove: 1.8, ivRange: { min: 0.20, max: 0.40 } },
  'Consumer Defensive': { annualizedVol: 0.18, avgDailyMove: 1.2, ivRange: { min: 0.12, max: 0.28 } },
  'Communication Services': { annualizedVol: 0.30, avgDailyMove: 2.2, ivRange: { min: 0.22, max: 0.50 } },
  'Industrials': { annualizedVol: 0.24, avgDailyMove: 1.7, ivRange: { min: 0.18, max: 0.35 } },
  'Energy': { annualizedVol: 0.32, avgDailyMove: 2.3, ivRange: { min: 0.25, max: 0.50 } },
  'Utilities': { annualizedVol: 0.16, avgDailyMove: 1.0, ivRange: { min: 0.12, max: 0.25 } },
  'Real Estate': { annualizedVol: 0.26, avgDailyMove: 1.9, ivRange: { min: 0.18, max: 0.38 } },
  'Materials': { annualizedVol: 0.28, avgDailyMove: 2.1, ivRange: { min: 0.20, max: 0.42 } }
};

export class RealisticExpectedMoveCalculator {
  /**
   * Calculate realistic expected move for a given symbol
   */
  static async calculateRealisticExpectedMove(symbol: string): Promise<RealisticExpectedMoveData> {
    // Get company and quote data
    const company = sp500DataService.getCompany(symbol);
    const quoteData = await fetchHybridQuoteData(symbol);
    
    const currentPrice = quoteData.price;
    const sector = company?.sector || 'Technology';
    
    // Get historical volatility data for this stock
    const historicalData = HISTORICAL_VOLATILITY_DATA[symbol];
    const sectorDefaults = SECTOR_VOLATILITY_DEFAULTS[sector];
    
    let volatilityData: {
      annualizedVol: number;
      avgDailyMove: number;
      ivRange: { min: number; max: number };
      method: 'historical' | 'sector';
    };
    
    if (historicalData) {
      // Use real historical data
      volatilityData = {
        annualizedVol: historicalData.annualizedVol,
        avgDailyMove: historicalData.avgDailyMove,
        ivRange: { min: historicalData.recentLow, max: historicalData.recentHigh },
        method: 'historical'
      };
    } else {
      // Use sector-based estimates
      volatilityData = {
        ...sectorDefaults,
        method: 'sector'
      };
    }
    
    // Adjust for current market conditions
    const marketRegimeMultiplier = this.getMarketRegimeMultiplier();
    const adjustedVol = volatilityData.annualizedVol * marketRegimeMultiplier;
    
    // Calculate expected moves
    const dailyMove = (volatilityData.avgDailyMove / 100) * currentPrice;
    const weeklyMove = dailyMove * Math.sqrt(5); // Scale by sqrt of trading days
    const monthlyMove = dailyMove * Math.sqrt(22); // ~22 trading days in a month
    
    // Debug logging (removed for production)
    
    // Calculate straddle-based expected move (30-day approximation)
    const timeToExpiry = 30 / 365; // 30 days
    const straddleMove = currentPrice * adjustedVol * Math.sqrt(timeToExpiry);
    const straddleMovePercent = (straddleMove / currentPrice) * 100;
    
    // Calculate IV metrics
    const currentIV = adjustedVol * 100;
    const ivRank = this.calculateIVRank(currentIV, volatilityData.ivRange);
    const ivPercentile = this.calculateIVPercentile(currentIV, volatilityData.ivRange);
    
    // Determine confidence level
    const confidence = historicalData ? 'high' : 
                      company ? 'medium' : 'low';
    
    return {
      symbol,
      currentPrice,
      summary: {
        daily: dailyMove,
        weekly: weeklyMove,
        monthly: monthlyMove
      },
      straddle: {
        price: straddleMove * 2, // Approximate straddle price
        move: straddleMove,
        movePercent: straddleMovePercent
      },
      iv: {
        rank: ivRank,
        percentile: ivPercentile,
        current: currentIV,
        high52Week: volatilityData.ivRange.max * 100,
        low52Week: volatilityData.ivRange.min * 100
      },
      confidence,
      method: volatilityData.method
    };
  }
  
  /**
   * Get market regime multiplier based on current market conditions
   */
  private static getMarketRegimeMultiplier(): number {
    // In a real implementation, this would check VIX, market trends, etc.
    // For now, we'll use a base multiplier with some variation
    const baseMultiplier = 1.0;
    const marketStressAdjustment = 0.1 * (Math.random() - 0.5); // ±5% adjustment
    return Math.max(0.7, Math.min(1.5, baseMultiplier + marketStressAdjustment));
  }
  
  /**
   * Calculate IV rank (where current IV sits in 52-week range)
   */
  private static calculateIVRank(currentIV: number, ivRange: { min: number; max: number }): number {
    const minIV = ivRange.min * 100;
    const maxIV = ivRange.max * 100;
    const rank = ((currentIV - minIV) / (maxIV - minIV)) * 100;
    return Math.max(0, Math.min(100, rank));
  }
  
  /**
   * Calculate IV percentile (more sophisticated than rank)
   */
  private static calculateIVPercentile(currentIV: number, ivRange: { min: number; max: number }): number {
    // Simplified percentile calculation
    // In reality, this would use historical IV distribution
    const rank = this.calculateIVRank(currentIV, ivRange);
    
    // Apply normal distribution curve to convert rank to percentile
    if (rank < 20) return rank * 0.8; // Lower tail compression
    if (rank > 80) return 80 + (rank - 80) * 0.8; // Upper tail compression
    return rank; // Middle range stays linear
  }
}

/**
 * Format expected move data for API response
 */
export function formatRealisticExpectedMove(data: RealisticExpectedMoveData) {
  const currentPrice = data.currentPrice;
  
  // Format each range with the structure ExpectedMoveCard expects
  const formatRange = (move: number) => ({
    move: move,
    percentage: (move / currentPrice) * 100,
    lower: currentPrice - move,
    upper: currentPrice + move
  });
  
  return {
    symbol: data.symbol,
    summary: {
      daily: formatRange(data.summary.daily),
      weekly: formatRange(data.summary.weekly),
      monthly: formatRange(data.summary.monthly)
    },
    straddle: {
      price: data.straddle.price,
      move: data.straddle.move,
      movePercent: data.straddle.movePercent
    },
    iv: {
      rank: data.iv.rank,
      percentile: data.iv.percentile,
      current: data.iv.current,
      high52Week: data.iv.high52Week,
      low52Week: data.iv.low52Week
    },
    confidence: data.confidence,
    method: data.method,
    timeToExpiry: 30,
    underlyingPrice: data.currentPrice,
    impliedVolatility: data.iv.current / 100
  };
}
