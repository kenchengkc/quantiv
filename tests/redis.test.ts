import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RedisCache, QuantivCache, Keys, formatDateForKey, checkRedisHealth } from '../lib/cache/redis';

// Create mock Redis methods
const mockRedisInstance = {
  setex: vi.fn(),
  get: vi.fn(),
  incr: vi.fn(),
  expire: vi.fn(),
  zadd: vi.fn(),
  zrange: vi.fn(),
  del: vi.fn(),
  exists: vi.fn(),
  ttl: vi.fn(),
  ping: vi.fn(),
};

// Mock Redis for tests
vi.mock('@upstash/redis', () => ({
  Redis: vi.fn().mockImplementation(() => mockRedisInstance),
}));

describe('Redis Cache', () => {
  beforeEach(() => {
    // Reset all mocks
    vi.clearAllMocks();
  });

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

  describe('RedisCache', () => {
    describe('setJson', () => {
      it('should set JSON data with TTL', async () => {
        mockRedisInstance.setex.mockResolvedValue('OK');
        
        const data = { test: 'value', number: 42 };
        await RedisCache.setJson('test:key', data, 300);
        
        expect(mockRedisInstance.setex).toHaveBeenCalledWith(
          'test:key',
          300,
          JSON.stringify(data)
        );
      });

      it('should use default TTL when not specified', async () => {
        mockRedisInstance.setex.mockResolvedValue('OK');
        
        await RedisCache.setJson('test:key', { data: 'test' });
        
        expect(mockRedisInstance.setex).toHaveBeenCalledWith(
          'test:key',
          120, // Default TTL
          expect.any(String)
        );
      });

      it('should handle Redis errors gracefully', async () => {
        mockRedisInstance.setex.mockRejectedValue(new Error('Redis connection failed'));
        
        // Should not throw
        await expect(RedisCache.setJson('test:key', { data: 'test' })).resolves.toBeUndefined();
      });
    });

    describe('getJson', () => {
      it('should get and parse JSON data', async () => {
        const data = { test: 'value', number: 42 };
        mockRedisInstance.get.mockResolvedValue(JSON.stringify(data));
        
        const result = await RedisCache.getJson('test:key');
        
        expect(mockRedisInstance.get).toHaveBeenCalledWith('test:key');
        expect(result).toEqual(data);
      });

      it('should return null for non-existent keys', async () => {
        mockRedisInstance.get.mockResolvedValue(null);
        
        const result = await RedisCache.getJson('test:key');
        
        expect(result).toBeNull();
      });

      it('should handle Redis errors gracefully', async () => {
        mockRedisInstance.get.mockRejectedValue(new Error('Redis connection failed'));
        
        const result = await RedisCache.getJson('test:key');
        
        expect(result).toBeNull();
      });
    });

    describe('increment', () => {
      it('should increment counter and return new value', async () => {
        mockRedisInstance.incr.mockResolvedValue(5);
        
        const result = await RedisCache.increment('counter:key');
        
        expect(mockRedisInstance.incr).toHaveBeenCalledWith('counter:key');
        expect(result).toBe(5);
      });

      it('should set TTL on first increment', async () => {
        mockRedisInstance.incr.mockResolvedValue(1); // First increment
        mockRedisInstance.expire.mockResolvedValue(1);
        
        await RedisCache.increment('counter:key', 3600);
        
        expect(mockRedisInstance.expire).toHaveBeenCalledWith('counter:key', 3600);
      });

      it('should not set TTL on subsequent increments', async () => {
        mockRedisInstance.incr.mockResolvedValue(5); // Not first increment
        
        await RedisCache.increment('counter:key', 3600);
        
        expect(mockRedisInstance.expire).not.toHaveBeenCalled();
      });

      it('should handle Redis errors gracefully', async () => {
        mockRedisInstance.incr.mockRejectedValue(new Error('Redis connection failed'));
        
        const result = await RedisCache.increment('counter:key');
        
        expect(result).toBe(0);
      });
    });
  });

  describe('QuantivCache', () => {
    describe('visitor count', () => {
      it('should increment visitor count for today', async () => {
        mockRedisInstance.incr.mockResolvedValue(42);
        
        const count = await QuantivCache.incrementVisitorCount();
        
        expect(count).toBe(42);
        expect(mockRedisInstance.incr).toHaveBeenCalledWith(
          expect.stringMatching(/^d:visits:\d{8}$/)
        );
      });

      it('should get visitor count for specific date', async () => {
        mockRedisInstance.get.mockResolvedValue('25');
        
        const count = await QuantivCache.getVisitorCount('20240119');
        
        expect(count).toBe(25);
        expect(mockRedisInstance.get).toHaveBeenCalledWith('d:visits:20240119');
      });

      it('should return 0 for missing visitor count', async () => {
        mockRedisInstance.get.mockResolvedValue(null);
        
        const count = await QuantivCache.getVisitorCount('20240119');
        
        expect(count).toBe(0);
      });
    });

    describe('expected move caching', () => {
      it('should cache expected move data', async () => {
        mockRedisInstance.setex.mockResolvedValue('OK');
        
        const data = {
          straddle: { abs: 10.50, pct: 7.0 },
          iv: { abs: 12.25, pct: 8.2 },
          bands: { oneSigma: { upper: 162.25, lower: 137.75 } }
        };
        
        await QuantivCache.cacheExpectedMove('AAPL', '2024-01-19', data, 180);
        
        expect(mockRedisInstance.setex).toHaveBeenCalledWith(
          'em:snap:AAPL:2024-01-19',
          180,
          expect.stringContaining('"symbol":"AAPL"')
        );
      });

      it('should get cached expected move data', async () => {
        const cachedData = {
          straddle: { abs: 10.50, pct: 7.0 },
          timestamp: '2024-01-19T10:30:00.000Z',
          symbol: 'AAPL'
        };
        
        mockRedisInstance.get.mockResolvedValue(JSON.stringify(cachedData));
        
        const result = await QuantivCache.getExpectedMove('AAPL', '2024-01-19');
        
        expect(result).toEqual(cachedData);
        expect(mockRedisInstance.get).toHaveBeenCalledWith('em:snap:AAPL:2024-01-19');
      });
    });

    describe('top movers', () => {
      it('should add symbol to top movers', async () => {
        mockRedisInstance.zadd.mockResolvedValue(1);
        mockRedisInstance.expire.mockResolvedValue(1);
        
        await QuantivCache.addTopMover('TSLA', 8.5, '20240119');
        
        expect(mockRedisInstance.zadd).toHaveBeenCalledWith(
          'em:top:20240119',
          { score: 8.5, member: 'TSLA' }
        );
        expect(mockRedisInstance.expire).toHaveBeenCalledWith('em:top:20240119', 24 * 60 * 60);
      });

      it('should get top movers for date', async () => {
        mockRedisInstance.zrange.mockResolvedValue(['TSLA', 8.5, 'AAPL', 7.2, 'NVDA', 6.8]);
        
        const topMovers = await QuantivCache.getTopMovers('20240119', 3);
        
        expect(topMovers).toEqual([
          { symbol: 'TSLA', expectedMovePct: 8.5 },
          { symbol: 'AAPL', expectedMovePct: 7.2 },
          { symbol: 'NVDA', expectedMovePct: 6.8 }
        ]);
      });
    });
  });

  describe('Utility Functions', () => {
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

    describe('checkRedisHealth', () => {
      it('should return connected status when Redis is healthy', async () => {
        mockRedisInstance.ping.mockResolvedValue('PONG');
        
        const health = await checkRedisHealth();
        
        expect(health.connected).toBe(true);
        expect(health.latency).toBeGreaterThan(0);
        expect(health.error).toBeUndefined();
      });

      it('should return disconnected status when Redis fails', async () => {
        mockRedisInstance.ping.mockRejectedValue(new Error('Connection timeout'));
        
        const health = await checkRedisHealth();
        
        expect(health.connected).toBe(false);
        expect(health.error).toBe('Connection timeout');
        expect(health.latency).toBeUndefined();
      });
    });
  });
});
