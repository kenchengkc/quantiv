import { describe, it, expect, beforeEach, vi } from 'vitest';
import { QuantivLRUCache, CacheInstances, getAllCacheStats, clearAllCaches, CacheKeys } from '../lib/cache/lru';

describe('QuantivLRUCache', () => {
  let cache: QuantivLRUCache<string>;

  beforeEach(() => {
    cache = new QuantivLRUCache('test-cache', {
      maxSize: 3,
      ttlMs: 1000, // 1 second for testing
      updateAgeOnGet: true
    });
  });

  describe('basic operations', () => {
    it('should set and get items', () => {
      cache.set('key1', 'value1');
      
      expect(cache.get('key1')).toBe('value1');
      expect(cache.has('key1')).toBe(true);
    });

    it('should return null for non-existent keys', () => {
      expect(cache.get('nonexistent')).toBeNull();
      expect(cache.has('nonexistent')).toBe(false);
    });

    it('should delete items', () => {
      cache.set('key1', 'value1');
      
      expect(cache.delete('key1')).toBe(true);
      expect(cache.get('key1')).toBeNull();
      expect(cache.delete('nonexistent')).toBe(false);
    });

    it('should clear all items', () => {
      cache.set('key1', 'value1');
      cache.set('key2', 'value2');
      
      cache.clear();
      
      expect(cache.get('key1')).toBeNull();
      expect(cache.get('key2')).toBeNull();
      expect(cache.getStats().size).toBe(0);
    });
  });

  describe('LRU eviction', () => {
    it('should evict least recently used items when at capacity', () => {
      cache.set('key1', 'value1');
      cache.set('key2', 'value2');
      cache.set('key3', 'value3');
      
      // Cache is now at capacity (3)
      expect(cache.getStats().size).toBe(3);
      
      // Adding a 4th item should evict the first
      cache.set('key4', 'value4');
      
      expect(cache.get('key1')).toBeNull(); // Evicted
      expect(cache.get('key2')).toBe('value2');
      expect(cache.get('key3')).toBe('value3');
      expect(cache.get('key4')).toBe('value4');
    });

    it('should update LRU order on get', () => {
      cache.set('key1', 'value1');
      cache.set('key2', 'value2');
      cache.set('key3', 'value3');
      
      // Access key1 to make it most recently used
      cache.get('key1');
      
      // Add key4, should evict key2 (least recently used)
      cache.set('key4', 'value4');
      
      expect(cache.get('key1')).toBe('value1'); // Still there
      expect(cache.get('key2')).toBeNull(); // Evicted
      expect(cache.get('key3')).toBe('value3');
      expect(cache.get('key4')).toBe('value4');
    });
  });

  describe('statistics', () => {
    it('should track hits and misses', () => {
      cache.set('key1', 'value1');
      
      // Hit
      cache.get('key1');
      cache.get('key1');
      
      // Miss
      cache.get('nonexistent');
      
      const stats = cache.getStats();
      expect(stats.hits).toBe(2);
      expect(stats.misses).toBe(1);
      expect(stats.hitRate).toBe(66.66666666666666);
    });

    it('should reset statistics on clear', () => {
      cache.set('key1', 'value1');
      cache.get('key1'); // Hit
      cache.get('nonexistent'); // Miss
      
      cache.clear();
      
      const stats = cache.getStats();
      expect(stats.hits).toBe(0);
      expect(stats.misses).toBe(0);
      expect(stats.hitRate).toBe(0);
    });
  });

  describe('getOrSet pattern', () => {
    it('should fetch data when not in cache', async () => {
      const fetchFn = vi.fn().mockResolvedValue('fetched-value');
      
      const result = await cache.getOrSet('key1', fetchFn);
      
      expect(result).toBe('fetched-value');
      expect(fetchFn).toHaveBeenCalledOnce();
      expect(cache.get('key1')).toBe('fetched-value');
    });

    it('should return cached data without fetching', async () => {
      const fetchFn = vi.fn().mockResolvedValue('fetched-value');
      
      cache.set('key1', 'cached-value');
      const result = await cache.getOrSet('key1', fetchFn);
      
      expect(result).toBe('cached-value');
      expect(fetchFn).not.toHaveBeenCalled();
    });

    it('should propagate fetch errors', async () => {
      const fetchFn = vi.fn().mockRejectedValue(new Error('Fetch failed'));
      
      await expect(cache.getOrSet('key1', fetchFn)).rejects.toThrow('Fetch failed');
      expect(cache.get('key1')).toBeNull();
    });
  });

  describe('peek operation', () => {
    it('should peek without updating LRU order', () => {
      cache.set('key1', 'value1');
      cache.set('key2', 'value2');
      cache.set('key3', 'value3');
      
      // Peek at key1 (shouldn't update LRU order)
      expect(cache.peek('key1')).toBe('value1');
      
      // Add key4, should still evict key1 (oldest)
      cache.set('key4', 'value4');
      
      expect(cache.get('key1')).toBeNull(); // Still evicted
    });
  });

  describe('TTL with custom values', () => {
    it('should respect custom TTL values', async () => {
      cache.set('key1', 'value1', 100); // 100ms TTL
      
      expect(cache.get('key1')).toBe('value1');
      
      // Wait for TTL to expire
      await new Promise(resolve => setTimeout(resolve, 150));
      
      expect(cache.get('key1')).toBeNull();
    });
  });

  describe('cache info', () => {
    it('should provide detailed cache information', () => {
      cache.set('key1', 'value1');
      cache.set('key2', 'value2');
      
      const info = cache.getInfo();
      
      expect(info.name).toBe('test-cache');
      expect(info.entries).toHaveLength(2);
      expect(info.entries[0]).toHaveProperty('key');
      expect(info.entries[0]).toHaveProperty('timestamp');
      expect(info.entries[0]).toHaveProperty('age');
      expect(info.stats).toHaveProperty('size');
    });
  });
});

describe('Cache Instances', () => {
  beforeEach(() => {
    clearAllCaches();
  });

  it('should have pre-configured cache instances', () => {
    expect(CacheInstances.optionsChain).toBeDefined();
    expect(CacheInstances.expectedMove).toBeDefined();
    expect(CacheInstances.earnings).toBeDefined();
    expect(CacheInstances.priceHistory).toBeDefined();
    expect(CacheInstances.ivSeries).toBeDefined();
  });

  it('should have different configurations for different caches', () => {
    const chainStats = CacheInstances.optionsChain.getStats();
    const earningsStats = CacheInstances.earnings.getStats();
    
    expect(chainStats.maxSize).toBe(200);
    expect(earningsStats.maxSize).toBe(100);
  });

  it('should work independently', () => {
    CacheInstances.optionsChain.set('AAPL:2024-01-19', 'chain-data');
    CacheInstances.expectedMove.set('AAPL:2024-01-19', 'em-data');
    
    expect(CacheInstances.optionsChain.get('AAPL:2024-01-19')).toBe('chain-data');
    expect(CacheInstances.expectedMove.get('AAPL:2024-01-19')).toBe('em-data');
    
    // Different caches, same key
    expect(CacheInstances.optionsChain.get('AAPL:2024-01-19')).not.toBe(
      CacheInstances.expectedMove.get('AAPL:2024-01-19')
    );
  });
});

describe('Cache Utilities', () => {
  beforeEach(() => {
    clearAllCaches();
  });

  describe('getAllCacheStats', () => {
    it('should return stats for all cache instances', () => {
      CacheInstances.optionsChain.set('test', 'data');
      CacheInstances.expectedMove.set('test', 'data');
      
      const allStats = getAllCacheStats();
      
      expect(allStats).toHaveProperty('optionsChain');
      expect(allStats).toHaveProperty('expectedMove');
      expect(allStats).toHaveProperty('earnings');
      expect(allStats).toHaveProperty('priceHistory');
      expect(allStats).toHaveProperty('ivSeries');
      
      expect(allStats.optionsChain.size).toBe(1);
      expect(allStats.expectedMove.size).toBe(1);
      expect(allStats.earnings.size).toBe(0);
    });
  });

  describe('clearAllCaches', () => {
    it('should clear all cache instances', () => {
      CacheInstances.optionsChain.set('test1', 'data1');
      CacheInstances.expectedMove.set('test2', 'data2');
      CacheInstances.earnings.set('test3', 'data3');
      
      clearAllCaches();
      
      const allStats = getAllCacheStats();
      Object.values(allStats).forEach(stats => {
        expect(stats.size).toBe(0);
      });
    });
  });

  describe('CacheKeys', () => {
    it('should generate consistent cache keys', () => {
      expect(CacheKeys.optionsChain('AAPL', '2024-01-19')).toBe('AAPL:2024-01-19');
      expect(CacheKeys.expectedMove('tsla', '2024-02-16')).toBe('TSLA:2024-02-16');
      expect(CacheKeys.earnings('spy')).toBe('SPY');
      expect(CacheKeys.priceHistory('QQQ', 30)).toBe('QQQ:30d');
      expect(CacheKeys.ivSeries('nvda', 252)).toBe('NVDA:252d');
    });

    it('should normalize symbols to uppercase', () => {
      expect(CacheKeys.optionsChain('aapl', '2024-01-19')).toBe('AAPL:2024-01-19');
      expect(CacheKeys.earnings('spy')).toBe('SPY');
    });
  });
});

describe('Complex Cache Scenarios', () => {
  let cache: QuantivLRUCache<{ data: string; computed: number }>;

  beforeEach(() => {
    cache = new QuantivLRUCache('complex-test', {
      maxSize: 5,
      ttlMs: 500,
      updateAgeOnGet: true
    });
  });

  it('should handle complex data types', () => {
    const complexData = {
      data: 'test-string',
      computed: 42.5
    };
    
    cache.set('complex', complexData);
    
    const retrieved = cache.get('complex');
    expect(retrieved).toEqual(complexData);
    expect(retrieved?.computed).toBe(42.5);
  });

  it('should maintain cache integrity under concurrent operations', async () => {
    const fetchFn1 = vi.fn().mockResolvedValue({ data: 'result1', computed: 1 });
    const fetchFn2 = vi.fn().mockResolvedValue({ data: 'result2', computed: 2 });
    
    // Simulate concurrent getOrSet calls
    const [result1, result2] = await Promise.all([
      cache.getOrSet('key1', fetchFn1),
      cache.getOrSet('key2', fetchFn2)
    ]);
    
    expect(result1).toEqual({ data: 'result1', computed: 1 });
    expect(result2).toEqual({ data: 'result2', computed: 2 });
    expect(fetchFn1).toHaveBeenCalledOnce();
    expect(fetchFn2).toHaveBeenCalledOnce();
  });

  it('should handle rapid cache operations', () => {
    // Fill cache rapidly
    for (let i = 0; i < 10; i++) {
      cache.set(`key${i}`, { data: `value${i}`, computed: i });
    }
    
    // Should only have last 5 items (maxSize = 5)
    expect(cache.getStats().size).toBe(5);
    
    // First 5 should be evicted
    for (let i = 0; i < 5; i++) {
      expect(cache.get(`key${i}`)).toBeNull();
    }
    
    // Last 5 should be present
    for (let i = 5; i < 10; i++) {
      expect(cache.get(`key${i}`)).toEqual({ data: `value${i}`, computed: i });
    }
  });
});
