import { describe, it, expect } from 'vitest';
import { Keys, formatDateForKey } from '../lib/cache/redis';

describe('Redis Cache Utilities', () => {
  describe('Keys', () => {
    it('should generate correct key formats', () => {
      expect(Keys.expectedMoveSnapshot('AAPL', '2024-01-19')).toBe('em:snap:AAPL:2024-01-19');
      expect(Keys.topMovers('20240119')).toBe('em:top:20240119');
      expect(Keys.ivSeries('tsla')).toBe('iv:series:TSLA');
      expect(Keys.dailyVisits('20240119')).toBe('d:visits:20240119');
      expect(Keys.optionsChain('NVDA', '2024-02-16')).toBe('chain:NVDA:2024-02-16');
      expect(Keys.earnings('spy')).toBe('earnings:SPY');
      expect(Keys.priceHistory('QQQ')).toBe('prices:QQQ');
    });

    it('should handle lowercase symbols correctly', () => {
      expect(Keys.expectedMoveSnapshot('aapl', '2024-01-19')).toBe('em:snap:AAPL:2024-01-19');
      expect(Keys.ivSeries('tsla')).toBe('iv:series:TSLA');
    });
  });

  describe('formatDateForKey', () => {
    it('should format date correctly', () => {
      const date = new Date('2024-01-19T10:30:00.000Z');
      const formatted = formatDateForKey(date);
      
      expect(formatted).toBe('20240119');
    });

    it('should use current date when not specified', () => {
      const formatted = formatDateForKey();
      
      expect(formatted).toMatch(/^\d{8}$/); // YYYYMMDD format
    });
  });
});
