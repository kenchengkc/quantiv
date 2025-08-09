import { describe, it, expect } from 'vitest';
import {
  computeExpectedMove,
  findATMData,
  calculateStraddleMove,
  calculateIVMove,
  calculatePriceBands,
  assessConfidence,
  formatExpectedMove,
  type ChainData
} from '../lib/services/expectedMove';

describe('Expected Move Service', () => {
  // Mock chain data for AAPL-like scenario
  const mockChain: ChainData = {
    spot: 150.00,
    strikes: [145, 147.5, 150, 152.5, 155],
    calls: [
      { strike: 145, mid: 6.50, bid: 6.25, ask: 6.75, iv: 0.25, volume: 150, openInterest: 500 },
      { strike: 147.5, mid: 4.75, bid: 4.50, ask: 5.00, iv: 0.24, volume: 200, openInterest: 800 },
      { strike: 150, mid: 3.25, bid: 3.00, ask: 3.50, iv: 0.23, volume: 500, openInterest: 1200 },
      { strike: 152.5, mid: 2.10, bid: 1.90, ask: 2.30, iv: 0.24, volume: 180, openInterest: 600 },
      { strike: 155, mid: 1.25, bid: 1.10, ask: 1.40, iv: 0.25, volume: 100, openInterest: 400 }
    ],
    puts: [
      { strike: 145, mid: 1.25, bid: 1.10, ask: 1.40, iv: 0.25, volume: 100, openInterest: 400 },
      { strike: 147.5, mid: 2.10, bid: 1.90, ask: 2.30, iv: 0.24, volume: 180, openInterest: 600 },
      { strike: 150, mid: 3.25, bid: 3.00, ask: 3.50, iv: 0.23, volume: 500, openInterest: 1200 },
      { strike: 152.5, mid: 4.75, bid: 4.50, ask: 5.00, iv: 0.24, volume: 200, openInterest: 800 },
      { strike: 155, mid: 6.50, bid: 6.25, ask: 6.75, iv: 0.25, volume: 150, openInterest: 500 }
    ],
    expiryDate: '2024-01-19',
    daysToExpiry: 30
  };

  // Mock chain with missing IV data
  const mockChainNoIV: ChainData = {
    spot: 100.00,
    strikes: [95, 100, 105],
    calls: [
      { strike: 95, mid: 6.50, bid: 6.25, ask: 6.75, volume: 50, openInterest: 200 },
      { strike: 100, mid: 2.50, bid: 2.25, ask: 2.75, volume: 100, openInterest: 500 },
      { strike: 105, mid: 0.75, bid: 0.65, ask: 0.85, volume: 30, openInterest: 150 }
    ],
    puts: [
      { strike: 95, mid: 0.75, bid: 0.65, ask: 0.85, volume: 30, openInterest: 150 },
      { strike: 100, mid: 2.50, bid: 2.25, ask: 2.75, volume: 100, openInterest: 500 },
      { strike: 105, mid: 6.50, bid: 6.25, ask: 6.75, volume: 50, openInterest: 200 }
    ],
    expiryDate: '2024-02-16',
    daysToExpiry: 45
  };

  describe('findATMData', () => {
    it('should find ATM data correctly', () => {
      const atmData = findATMData(mockChain);
      
      expect(atmData.strike).toBe(150);
      expect(atmData.callMid).toBe(3.25);
      expect(atmData.putMid).toBe(3.25);
      expect(atmData.iv).toBe(0.23);
      expect(atmData.T).toBeCloseTo(30/365, 3);
    });

    it('should handle missing IV by averaging call and put IVs', () => {
      const chainWithMixedIV = {
        ...mockChain,
        calls: mockChain.calls.map(c => c.strike === 150 ? { ...c, iv: 0.22 } : c),
        puts: mockChain.puts.map(p => p.strike === 150 ? { ...p, iv: 0.24 } : p)
      };
      
      const atmData = findATMData(chainWithMixedIV);
      expect(atmData.iv).toBeCloseTo(0.23, 2); // Average of 0.22 and 0.24
    });

    it('should estimate IV when not available', () => {
      const atmData = findATMData(mockChainNoIV);
      
      expect(atmData.strike).toBe(100);
      expect(atmData.iv).toBeGreaterThan(0.05);
      expect(atmData.iv).toBeLessThan(2.0);
    });

    it('should throw error when ATM options not found', () => {
      const invalidChain = {
        ...mockChain,
        calls: mockChain.calls.filter(c => c.strike !== 150),
        puts: mockChain.puts.filter(p => p.strike !== 150)
      };
      
      expect(() => findATMData(invalidChain)).toThrow('ATM options not found');
    });
  });

  describe('calculateStraddleMove', () => {
    it('should calculate straddle move correctly', () => {
      const atmData = findATMData(mockChain);
      const straddleMove = calculateStraddleMove(atmData, mockChain.spot);
      
      expect(straddleMove.abs).toBe(6.50); // 3.25 + 3.25
      expect(straddleMove.pct).toBeCloseTo(4.33, 2); // 6.50/150 * 100
    });

    it('should handle different straddle prices', () => {
      const atmData = { strike: 100, callMid: 4.0, putMid: 3.0, iv: 0.20, T: 0.25 };
      const straddleMove = calculateStraddleMove(atmData, 100);
      
      expect(straddleMove.abs).toBe(7.0);
      expect(straddleMove.pct).toBeCloseTo(7.0, 10);
    });
  });

  describe('calculateIVMove', () => {
    it('should calculate IV move correctly', () => {
      const atmData = findATMData(mockChain);
      const ivMove = calculateIVMove(atmData, mockChain.spot);
      
      // Expected: 150 * 0.23 * sqrt(30/365) ≈ 150 * 0.23 * 0.287 ≈ 9.90
      expect(ivMove.abs).toBeCloseTo(9.90, 1);
      expect(ivMove.pct).toBeCloseTo(6.60, 1);
    });

    it('should handle different IV and time scenarios', () => {
      const atmData = { strike: 100, callMid: 2.5, putMid: 2.5, iv: 0.30, T: 0.25 };
      const ivMove = calculateIVMove(atmData, 100);
      
      // Expected: 100 * 0.30 * sqrt(0.25) = 100 * 0.30 * 0.5 = 15
      expect(ivMove.abs).toBe(15);
      expect(ivMove.pct).toBe(15);
    });
  });

  describe('calculatePriceBands', () => {
    it('should calculate price bands correctly', () => {
      const bands = calculatePriceBands(150, 10);
      
      expect(bands.oneSigma.upper).toBe(160);
      expect(bands.oneSigma.lower).toBe(140);
      expect(bands.twoSigma.upper).toBe(170);
      expect(bands.twoSigma.lower).toBe(130);
    });

    it('should handle edge cases', () => {
      const bands = calculatePriceBands(50, 5);
      
      expect(bands.oneSigma.upper).toBe(55);
      expect(bands.oneSigma.lower).toBe(45);
      expect(bands.twoSigma.upper).toBe(60);
      expect(bands.twoSigma.lower).toBe(40);
    });
  });

  describe('assessConfidence', () => {
    it('should assess high confidence for good data', () => {
      const atmData = findATMData(mockChain);
      const confidence = assessConfidence(mockChain, atmData);
      
      // High volume, tight spreads, consistent IVs
      expect(confidence.straddle).toBe('high');
      expect(confidence.iv).toBe('high');
    });

    it('should assess low confidence for poor data', () => {
      const poorChain = {
        ...mockChain,
        calls: mockChain.calls.map(c => c.strike === 150 ? 
          { ...c, bid: 2.0, ask: 4.5, volume: 5 } : c), // Wide spread, low volume
        puts: mockChain.puts.map(p => p.strike === 150 ? 
          { ...p, bid: 2.0, ask: 4.5, volume: 5 } : p)
      };
      
      const atmData = findATMData(poorChain);
      const confidence = assessConfidence(poorChain, atmData);
      
      expect(confidence.straddle).toBe('low');
    });

    it('should assess low confidence when IV data is missing', () => {
      const atmData = findATMData(mockChainNoIV);
      const confidence = assessConfidence(mockChainNoIV, atmData);
      
      expect(confidence.iv).toBe('low');
    });
  });

  describe('computeExpectedMove', () => {
    it('should compute complete expected move result', () => {
      const result = computeExpectedMove(mockChain);
      
      expect(result.straddle.abs).toBe(6.50);
      expect(result.straddle.pct).toBeCloseTo(4.33, 2);
      
      expect(result.iv.abs).toBeCloseTo(9.90, 1);
      expect(result.iv.pct).toBeCloseTo(6.60, 1);
      
      expect(result.bands.oneSigma.upper).toBeCloseTo(159.90, 1);
      expect(result.bands.oneSigma.lower).toBeCloseTo(140.10, 1);
      expect(result.bands.twoSigma.upper).toBeCloseTo(169.80, 1);
      expect(result.bands.twoSigma.lower).toBeCloseTo(130.20, 1);
      
      expect(result.confidence.straddle).toBe('high');
      expect(result.confidence.iv).toBe('high');
    });

    it('should handle chain with no IV data', () => {
      const result = computeExpectedMove(mockChainNoIV);
      
      expect(result.straddle.abs).toBe(5.0); // 2.5 + 2.5
      expect(result.straddle.pct).toBe(5.0);
      
      expect(result.iv.abs).toBeGreaterThan(0);
      expect(result.iv.pct).toBeGreaterThan(0);
      
      expect(result.confidence.iv).toBe('low');
    });
  });

  describe('formatExpectedMove', () => {
    it('should format expected move for display', () => {
      const result = computeExpectedMove(mockChain);
      const formatted = formatExpectedMove(result, mockChain.spot);
      
      expect(formatted.straddle.display).toMatch(/±\$6\.50 \(4\.3%\)/);
      expect(formatted.straddle.confidence).toBe('high');
      
      expect(formatted.iv.display).toMatch(/±\$9\.\d+ \(6\.\d%\)/);
      expect(formatted.iv.confidence).toBe('high');
      
      expect(formatted.bands.oneSigma).toMatch(/\$140\.\d+ - \$159\.\d+/);
      expect(formatted.bands.twoSigma).toMatch(/\$130\.\d+ - \$169\.\d+/);
    });

    it('should handle different confidence levels', () => {
      const result = computeExpectedMove(mockChainNoIV);
      const formatted = formatExpectedMove(result, mockChainNoIV.spot);
      
      expect(formatted.straddle.confidence).toBeDefined();
      expect(formatted.iv.confidence).toBe('low');
    });
  });

  describe('Edge Cases', () => {
    it('should handle very short time to expiry', () => {
      const shortTermChain = {
        ...mockChain,
        daysToExpiry: 1
      };
      
      const result = computeExpectedMove(shortTermChain);
      
      // Should still calculate but with lower confidence
      expect(result.iv.abs).toBeGreaterThan(0);
      expect(result.confidence.straddle).not.toBe('high');
      expect(result.confidence.iv).not.toBe('high');
    });

    it('should handle very long time to expiry', () => {
      const longTermChain = {
        ...mockChain,
        daysToExpiry: 365
      };
      
      const result = computeExpectedMove(longTermChain);
      
      // IV move should be much larger for longer time
      expect(result.iv.abs).toBeGreaterThan(result.straddle.abs);
    });

    it('should handle extreme volatility', () => {
      const highVolChain = {
        ...mockChain,
        calls: mockChain.calls.map(c => ({ ...c, iv: 1.0 })), // 100% IV
        puts: mockChain.puts.map(p => ({ ...p, iv: 1.0 }))
      };
      
      const result = computeExpectedMove(highVolChain);
      
      expect(result.iv.abs).toBeGreaterThan(30); // Should be very large
      expect(result.iv.pct).toBeGreaterThan(20);
    });
  });
});
