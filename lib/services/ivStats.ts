/**
 * IV Statistics Service
 * Calculates IV rank and percentile from historical IV data
 * Provides context for current implied volatility levels
 */

export interface IVDataPoint {
  date: string;
  iv: number;
  close?: number;
}

export interface IVStatsResult {
  rank: number;        // 0-1 scale where 1 = highest IV in period
  percentile: number;  // 0-100 scale, percentage of days with IV <= today
  current: number;     // Current IV level
  min: number;         // Minimum IV in period
  max: number;         // Maximum IV in period
  mean: number;        // Average IV in period
  median: number;      // Median IV in period
  stdDev: number;      // Standard deviation of IV
  daysInSample: number; // Number of days in the dataset
}

export interface IVContext {
  level: 'extremely-low' | 'low' | 'below-average' | 'average' | 'above-average' | 'high' | 'extremely-high';
  description: string;
  color: 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple';
  recommendation: 'strong-sell' | 'sell' | 'neutral' | 'buy' | 'strong-buy';
}

/**
 * Calculate IV rank and percentile from historical data
 */
export function calculateIVStats(history: IVDataPoint[], currentIV: number): IVStatsResult {
  if (history.length === 0) {
    throw new Error('No historical IV data provided');
  }

  const ivValues = history.map(d => d.iv).filter(iv => iv > 0 && iv < 10); // Filter outliers
  
  if (ivValues.length === 0) {
    throw new Error('No valid IV data points found');
  }

  const sortedIVs = [...ivValues].sort((a, b) => a - b);
  const min = sortedIVs[0];
  const max = sortedIVs[sortedIVs.length - 1];
  
  // Calculate rank (0-1 scale)
  const rank = max === min ? 0.5 : (currentIV - min) / (max - min);
  
  // Calculate percentile (0-100 scale)
  const belowOrEqual = ivValues.filter(iv => iv <= currentIV).length;
  const percentile = (belowOrEqual / ivValues.length) * 100;
  
  // Calculate statistics
  const mean = ivValues.reduce((sum, iv) => sum + iv, 0) / ivValues.length;
  
  // Calculate median properly for even/odd number of elements
  const median = sortedIVs.length % 2 === 1
    ? sortedIVs[Math.floor(sortedIVs.length / 2)]
    : (sortedIVs[sortedIVs.length / 2 - 1] + sortedIVs[sortedIVs.length / 2]) / 2;
  
  const variance = ivValues.reduce((sum, iv) => sum + Math.pow(iv - mean, 2), 0) / ivValues.length;
  const stdDev = Math.sqrt(variance);

  return {
    rank: Math.max(0, Math.min(1, rank)),
    percentile: Math.max(0, Math.min(100, percentile)),
    current: currentIV,
    min,
    max,
    mean,
    median,
    stdDev,
    daysInSample: ivValues.length
  };
}

/**
 * Get IV context and interpretation
 */
export function getIVContext(stats: IVStatsResult): IVContext {
  const { rank, percentile } = stats;
  
  if (percentile >= 90) {
    return {
      level: 'extremely-high',
      description: `IV is in the top 10% of the ${stats.daysInSample}-day range. Options are very expensive.`,
      color: 'red',
      recommendation: 'strong-sell'
    };
  } else if (percentile >= 75) {
    return {
      level: 'high',
      description: `IV is in the top 25% of the ${stats.daysInSample}-day range. Options are expensive.`,
      color: 'orange',
      recommendation: 'sell'
    };
  } else if (percentile >= 60) {
    return {
      level: 'above-average',
      description: `IV is above average for the ${stats.daysInSample}-day period. Options are moderately expensive.`,
      color: 'yellow',
      recommendation: 'neutral'
    };
  } else if (percentile >= 40) {
    return {
      level: 'average',
      description: `IV is near the average for the ${stats.daysInSample}-day period. Options are fairly priced.`,
      color: 'green',
      recommendation: 'neutral'
    };
  } else if (percentile >= 25) {
    return {
      level: 'below-average',
      description: `IV is below average for the ${stats.daysInSample}-day period. Options are moderately cheap.`,
      color: 'blue',
      recommendation: 'neutral'
    };
  } else if (percentile >= 10) {
    return {
      level: 'low',
      description: `IV is in the bottom 25% of the ${stats.daysInSample}-day range. Options are cheap.`,
      color: 'blue',
      recommendation: 'buy'
    };
  } else {
    return {
      level: 'extremely-low',
      description: `IV is in the bottom 10% of the ${stats.daysInSample}-day range. Options are very cheap.`,
      color: 'purple',
      recommendation: 'strong-buy'
    };
  }
}

/**
 * Generate IV sparkline data for visualization
 */
export function generateIVSparkline(history: IVDataPoint[], currentIV: number, points: number = 50): Array<{
  date: string;
  iv: number;
  isToday: boolean;
}> {
  if (history.length === 0) {
    // Return just today's point when no history
    return [{
      date: new Date().toISOString().split('T')[0],
      iv: currentIV,
      isToday: true
    }];
  }
  
  // Sort by date and take the most recent points
  const sortedHistory = [...history]
    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
    .slice(-points);
  
  const sparklineData = sortedHistory.map(point => ({
    date: point.date,
    iv: point.iv,
    isToday: false
  }));
  
  // Add current IV as today's point if not already included
  const today = new Date().toISOString().split('T')[0];
  const hasToday = sparklineData.some(point => point.date === today);
  
  if (!hasToday) {
    sparklineData.push({
      date: today,
      iv: currentIV,
      isToday: true
    });
  } else {
    // Mark the last point as today
    sparklineData[sparklineData.length - 1].isToday = true;
  }
  
  return sparklineData;
}

/**
 * Calculate IV percentile bands for visualization
 */
export function calculateIVBands(history: IVDataPoint[]): {
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
} {
  if (history.length === 0) {
    throw new Error('No historical data provided');
  }
  
  const ivValues = history.map(d => d.iv).filter(iv => iv > 0 && iv < 10).sort((a, b) => a - b);
  
  if (ivValues.length === 0) {
    throw new Error('No valid IV data points found');
  }
  
  const getPercentile = (arr: number[], p: number): number => {
    if (arr.length === 1) return arr[0];
    
    const index = (p / 100) * (arr.length - 1);
    const lower = Math.floor(index);
    const upper = Math.ceil(index);
    
    if (lower === upper) {
      return arr[lower];
    }
    
    // Linear interpolation between the two nearest values
    const weight = index - lower;
    return arr[lower] * (1 - weight) + arr[upper] * weight;
  };
  
  return {
    p10: getPercentile(ivValues, 10),
    p25: getPercentile(ivValues, 25),
    p50: getPercentile(ivValues, 50),
    p75: getPercentile(ivValues, 75),
    p90: getPercentile(ivValues, 90)
  };
}

/**
 * Detect IV expansion/contraction trends
 */
export function detectIVTrend(history: IVDataPoint[], lookbackDays: number = 10): {
  trend: 'expanding' | 'contracting' | 'stable';
  strength: 'weak' | 'moderate' | 'strong';
  change: number; // Percentage change over lookback period
} {
  if (history.length < lookbackDays) {
    return { trend: 'stable', strength: 'weak', change: 0 };
  }
  
  const recent = history.slice(-lookbackDays);
  const firstIV = recent[0].iv;
  const lastIV = recent[recent.length - 1].iv;
  
  const change = ((lastIV - firstIV) / firstIV) * 100;
  const absChange = Math.abs(change);
  
  let trend: 'expanding' | 'contracting' | 'stable';
  if (change > 2) {
    trend = 'expanding';
  } else if (change < -2) {
    trend = 'contracting';
  } else {
    trend = 'stable';
  }
  
  let strength: 'weak' | 'moderate' | 'strong';
  if (absChange < 5) {
    strength = 'weak';
  } else if (absChange < 15) {
    strength = 'moderate';
  } else {
    strength = 'strong';
  }
  
  return { trend, strength, change };
}

/**
 * Format IV stats for display
 */
export function formatIVStats(stats: IVStatsResult): {
  rank: string;
  percentile: string;
  current: string;
  range: string;
  context: IVContext;
} {
  const context = getIVContext(stats);
  
  return {
    rank: `${(stats.rank * 100).toFixed(0)}%`,
    percentile: `${stats.percentile.toFixed(0)}th percentile`,
    current: `${(stats.current * 100).toFixed(1)}%`,
    range: `${(stats.min * 100).toFixed(1)}% - ${(stats.max * 100).toFixed(1)}%`,
    context
  };
}

/**
 * Utility function to create mock IV history for testing
 */
export function createMockIVHistory(days: number, baseIV: number = 0.25, volatility: number = 0.05): IVDataPoint[] {
  const history: IVDataPoint[] = [];
  const startDate = new Date();
  startDate.setDate(startDate.getDate() - days);
  
  for (let i = 0; i < days; i++) {
    const date = new Date(startDate);
    date.setDate(date.getDate() + i);
    
    // Generate IV with some randomness and mean reversion
    const randomFactor = (Math.random() - 0.5) * volatility;
    const meanReversion = (baseIV - (history[i - 1]?.iv || baseIV)) * 0.1;
    const iv = Math.max(0.05, Math.min(2.0, (history[i - 1]?.iv || baseIV) + randomFactor + meanReversion));
    
    history.push({
      date: date.toISOString().split('T')[0],
      iv: iv,
      close: 100 + Math.random() * 20 - 10 // Mock stock price
    });
  }
  
  return history;
}
