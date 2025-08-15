import { describe, it, expect } from 'vitest';
import {
  calculateIVStats,
  getIVContext,
  generateIVSparkline,
  calculateIVBands,
  detectIVTrend,
  formatIVStats,

  type IVDataPoint
} from '../lib/services/ivStats';

describe('IV Statistics Service', () => {
  // Mock IV history data
  const mockIVHistory: IVDataPoint[] = [
    { date: '2024-01-01', iv: 0.15 },
    { date: '2024-01-02', iv: 0.18 },
    { date: '2024-01-03', iv: 0.22 },
    { date: '2024-01-04', iv: 0.25 },
    { date: '2024-01-05', iv: 0.30 },
    { date: '2024-01-06', iv: 0.35 },
    { date: '2024-01-07', iv: 0.40 },
    { date: '2024-01-08', iv: 0.45 },
    { date: '2024-01-09', iv: 0.50 },
    { date: '2024-01-10', iv: 0.55 }
  ];

  describe('calculateIVStats', () => {
    it('should calculate IV stats correctly', () => {
      const currentIV = 0.35;
      const stats = calculateIVStats(mockIVHistory, currentIV);
      
      expect(stats.current).toBe(0.35);
      expect(stats.min).toBe(0.15);
      expect(stats.max).toBe(0.55);
      expect(stats.daysInSample).toBe(10);
      
      // Rank should be (0.35 - 0.15) / (0.55 - 0.15) = 0.5
      expect(stats.rank).toBeCloseTo(0.5, 2);
      
      // Percentile: 6 values <= 0.35 out of 10 = 60%
      expect(stats.percentile).toBe(60);
      
      // Mean should be (0.15 + 0.18 + ... + 0.55) / 10 = 0.335
      expect(stats.mean).toBeCloseTo(0.335, 2);
      
      // Median should be (0.30 + 0.35) / 2 = 0.325
      expect(stats.median).toBeCloseTo(0.325, 3);
      
      expect(stats.stdDev).toBeGreaterThan(0);
    });

    it('should handle edge case where current IV equals min', () => {
      const currentIV = 0.15;
      const stats = calculateIVStats(mockIVHistory, currentIV);
      
      expect(stats.rank).toBe(0);
      expect(stats.percentile).toBe(10); // 1 out of 10 values
    });

    it('should handle edge case where current IV equals max', () => {
      const currentIV = 0.55;
      const stats = calculateIVStats(mockIVHistory, currentIV);
      
      expect(stats.rank).toBe(1);
      expect(stats.percentile).toBe(100); // All values <= current
    });

    it('should handle case where all IVs are the same', () => {
      const uniformHistory = Array(5).fill(null).map((_, i) => ({
        date: `2024-01-0${i + 1}`,
        iv: 0.25
      }));
      
      const stats = calculateIVStats(uniformHistory, 0.25);
      
      expect(stats.rank).toBe(0.5); // Default when min === max
      expect(stats.percentile).toBe(100);
      expect(stats.min).toBe(0.25);
      expect(stats.max).toBe(0.25);
      expect(stats.stdDev).toBe(0);
    });

    it('should filter out invalid IV values', () => {
      const historyWithOutliers = [
        ...mockIVHistory,
        { date: '2024-01-11', iv: -0.1 }, // Negative IV
        { date: '2024-01-12', iv: 15.0 },  // Extremely high IV
        { date: '2024-01-13', iv: 0 }      // Zero IV
      ];
      
      const stats = calculateIVStats(historyWithOutliers, 0.35);
      
      // Should only use the original 10 valid data points
      expect(stats.daysInSample).toBe(10);
      expect(stats.min).toBe(0.15);
      expect(stats.max).toBe(0.55);
    });

    it('should throw error for empty history', () => {
      expect(() => calculateIVStats([], 0.25)).toThrow('No historical IV data provided');
    });

    it('should throw error when no valid data points', () => {
      const invalidHistory = [
        { date: '2024-01-01', iv: -0.1 },
        { date: '2024-01-02', iv: 15.0 }
      ];
      
      expect(() => calculateIVStats(invalidHistory, 0.25)).toThrow('No valid IV data points found');
    });
  });

  describe('getIVContext', () => {
    it('should return extremely-high context for 90th+ percentile', () => {
      const stats = { percentile: 95, daysInSample: 252 } as any;
      const context = getIVContext(stats);
      
      expect(context.level).toBe('extremely-high');
      expect(context.color).toBe('red');
      expect(context.recommendation).toBe('strong-sell');
      expect(context.description).toContain('top 10%');
    });

    it('should return high context for 75th-89th percentile', () => {
      const stats = { percentile: 80, daysInSample: 252 } as any;
      const context = getIVContext(stats);
      
      expect(context.level).toBe('high');
      expect(context.color).toBe('orange');
      expect(context.recommendation).toBe('sell');
      expect(context.description).toContain('top 25%');
    });

    it('should return average context for 40th-60th percentile', () => {
      const stats = { percentile: 50, daysInSample: 252 } as any;
      const context = getIVContext(stats);
      
      expect(context.level).toBe('average');
      expect(context.color).toBe('green');
      expect(context.recommendation).toBe('neutral');
      expect(context.description).toContain('average');
    });

    it('should return low context for 10th-25th percentile', () => {
      const stats = { percentile: 20, daysInSample: 252 } as any;
      const context = getIVContext(stats);
      
      expect(context.level).toBe('low');
      expect(context.color).toBe('blue');
      expect(context.recommendation).toBe('buy');
      expect(context.description).toContain('bottom 25%');
    });

    it('should return extremely-low context for <10th percentile', () => {
      const stats = { percentile: 5, daysInSample: 252 } as any;
      const context = getIVContext(stats);
      
      expect(context.level).toBe('extremely-low');
      expect(context.color).toBe('purple');
      expect(context.recommendation).toBe('strong-buy');
      expect(context.description).toContain('bottom 10%');
    });
  });

  describe('generateIVSparkline', () => {
    it('should generate sparkline data with correct structure', () => {
      const sparkline = generateIVSparkline(mockIVHistory, 0.35, 5);
      
      expect(sparkline).toHaveLength(6); // 5 historical + 1 current
      expect(sparkline[sparkline.length - 1].isToday).toBe(true);
      expect(sparkline[sparkline.length - 1].iv).toBe(0.35);
      
      sparkline.slice(0, -1).forEach(point => {
        expect(point.isToday).toBe(false);
        expect(point.date).toBeDefined();
        expect(point.iv).toBeGreaterThan(0);
      });
    });

    it('should handle empty history', () => {
      const sparkline = generateIVSparkline([], 0.25);
      expect(sparkline).toHaveLength(1);
      expect(sparkline[0].isToday).toBe(true);
      expect(sparkline[0].iv).toBe(0.25);
    });

    it('should limit to requested number of points', () => {
      const sparkline = generateIVSparkline(mockIVHistory, 0.35, 3);
      expect(sparkline).toHaveLength(4); // 3 historical + 1 current
    });
  });

  describe('calculateIVBands', () => {
    it('should calculate percentile bands correctly', () => {
      const bands = calculateIVBands(mockIVHistory);
      
      expect(bands.p10).toBeCloseTo(0.177, 2); // Linear interpolation (10th percentile)
      expect(bands.p25).toBeCloseTo(0.2275, 2); // Linear interpolation (25th percentile)
      expect(bands.p50).toBeCloseTo(0.325, 2); // Linear interpolation (50th percentile)
      expect(bands.p75).toBeCloseTo(0.4375, 2); // Linear interpolation (75th percentile)
      expect(bands.p90).toBeCloseTo(0.505, 2); // Linear interpolation (90th percentile)
    });

    it('should handle single data point', () => {
      const singlePoint = [{ date: '2024-01-01', iv: 0.25 }];
      const bands = calculateIVBands(singlePoint);
      
      expect(bands.p10).toBe(0.25);
      expect(bands.p25).toBe(0.25);
      expect(bands.p50).toBe(0.25);
      expect(bands.p75).toBe(0.25);
      expect(bands.p90).toBe(0.25);
    });

    it('should throw error for empty history', () => {
      expect(() => calculateIVBands([])).toThrow('No historical data provided');
    });
  });

  describe('detectIVTrend', () => {
    it('should detect expanding trend', () => {
      // Create history with increasing IV
      const expandingHistory = Array(15).fill(null).map((_, i) => ({
        date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        iv: 0.20 + (i * 0.02) // Increasing from 0.20 to 0.48
      }));
      
      const trend = detectIVTrend(expandingHistory, 10);
      
      expect(trend.trend).toBe('expanding');
      expect(trend.change).toBeGreaterThan(10); // Should be significant increase
      expect(trend.strength).toBe('strong');
    });

    it('should detect contracting trend', () => {
      // Create history with decreasing IV
      const contractingHistory = Array(15).fill(null).map((_, i) => ({
        date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        iv: 0.50 - (i * 0.02) // Decreasing from 0.50 to 0.22
      }));
      
      const trend = detectIVTrend(contractingHistory, 10);
      
      expect(trend.trend).toBe('contracting');
      expect(trend.change).toBeLessThan(-10); // Should be significant decrease
      expect(trend.strength).toBe('strong');
    });

    it('should detect stable trend', () => {
      // Create history with truly stable IV (no random variations for test consistency)
      const stableHistory = Array(15).fill(null).map((_, i) => ({
        date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        iv: 0.25 + (i % 2 === 0 ? 0.001 : -0.001) // Very small alternating variations
      }));
      
      const trend = detectIVTrend(stableHistory, 10);
      
      expect(trend.trend).toBe('stable');
      expect(Math.abs(trend.change)).toBeLessThan(5);
    });

    it('should handle insufficient data', () => {
      const shortHistory = [
        { date: '2024-01-01', iv: 0.25 },
        { date: '2024-01-02', iv: 0.26 }
      ];
      
      const trend = detectIVTrend(shortHistory, 10);
      
      expect(trend.trend).toBe('stable');
      expect(trend.strength).toBe('weak');
      expect(trend.change).toBe(0);
    });
  });

  describe('formatIVStats', () => {
    it('should format IV stats for display', () => {
      const stats = calculateIVStats(mockIVHistory, 0.35);
      const formatted = formatIVStats(stats);
      
      expect(formatted.rank).toBe('50%');
      expect(formatted.percentile).toBe('60th percentile');
      expect(formatted.current).toBe('35.0%');
      expect(formatted.range).toBe('15.0% - 55.0%');
      expect(formatted.context.level).toBe('above-average');
    });

    it('should handle extreme values', () => {
      const stats = calculateIVStats(mockIVHistory, 0.55);
      const formatted = formatIVStats(stats);
      
      expect(formatted.rank).toBe('100%');
      expect(formatted.percentile).toBe('100th percentile');
      expect(formatted.context.level).toBe('extremely-high');
    });
  });

  describe('Integration Tests', () => {
    it('should work with realistic IV data', () => {
      // Use static test data instead of mock generator
      const history: IVDataPoint[] = [
        { date: '2024-01-01', iv: 0.25, close: 100 },
        { date: '2024-01-02', iv: 0.26, close: 101 },
        { date: '2024-01-03', iv: 0.24, close: 99 },
        { date: '2024-01-04', iv: 0.27, close: 102 },
        { date: '2024-01-05', iv: 0.23, close: 98 }
      ];
      const currentIV = 0.35;
      
      const stats = calculateIVStats(history, currentIV);
      const context = getIVContext(stats);
      const sparkline = generateIVSparkline(history, currentIV, 30);
      const bands = calculateIVBands(history);
      const trend = detectIVTrend(history, 20);
      
      // All functions should work together without errors
      expect(stats.daysInSample).toBe(252);
      expect(context.level).toBeDefined();
      expect(sparkline.length).toBeGreaterThan(0);
      expect(bands.p50).toBeGreaterThan(0);
      expect(trend.trend).toBeDefined();
    });

    it('should handle edge case with very low IV', () => {
      const lowIVHistory = Array(30).fill(null).map((_, i) => ({
        date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        iv: 0.08 + Math.random() * 0.02 // Very low IV range
      }));
      
      const stats = calculateIVStats(lowIVHistory, 0.06);
      const context = getIVContext(stats);
      
      expect(stats.rank).toBeCloseTo(0, 1);
      expect(context.level).toBe('extremely-low');
      expect(context.recommendation).toBe('strong-buy');
    });

    it('should handle edge case with very high IV', () => {
      const highIVHistory = Array(30).fill(null).map((_, i) => ({
        date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        iv: 0.80 + Math.random() * 0.20 // Very high IV range
      }));
      
      const stats = calculateIVStats(highIVHistory, 1.10);
      const context = getIVContext(stats);
      
      expect(stats.rank).toBeCloseTo(1, 1);
      expect(context.level).toBe('extremely-high');
      expect(context.recommendation).toBe('strong-sell');
    });
  });
});
