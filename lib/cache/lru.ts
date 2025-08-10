/**
 * LRU Cache for L1 (in-process) caching
 * Used for fast API response caching within each API route
 */

import { LRUCache } from 'lru-cache';

export interface CacheOptions {
  maxSize?: number;
  ttlMs?: number;
  updateAgeOnGet?: boolean;
}

export interface CacheEntry<T> {
  data: T;
  timestamp: number;
  key: string;
}

/**
 * Generic LRU cache wrapper with TTL and statistics
 */
export class QuantivLRUCache<T> {
  private cache: LRUCache<string, CacheEntry<T>>;
  private hits: number = 0;
  private misses: number = 0;
  private name: string;

  constructor(name: string, options: CacheOptions = {}) {
    this.name = name;
    this.cache = new LRUCache({
      max: options.maxSize || 100,
      ttl: options.ttlMs || 60 * 1000, // 1 minute default
      updateAgeOnGet: options.updateAgeOnGet ?? true,
      dispose: (value, key) => {
        // Optional cleanup when items are evicted
        console.debug(`[${this.name}] Evicted cache entry: ${key}`);
      }
    });
  }

  /**
   * Get item from cache
   */
  get(key: string): T | null {
    const entry = this.cache.get(key);
    
    if (entry) {
      this.hits++;
      return entry.data;
    } else {
      this.misses++;
      return null;
    }
  }

  /**
   * Set item in cache
   */
  set(key: string, data: T, ttlMs?: number): void {
    const entry: CacheEntry<T> = {
      data,
      timestamp: Date.now(),
      key
    };

    if (ttlMs) {
      this.cache.set(key, entry, { ttl: ttlMs });
    } else {
      this.cache.set(key, entry);
    }
  }

  /**
   * Check if key exists in cache
   */
  has(key: string): boolean {
    return this.cache.has(key);
  }

  /**
   * Delete item from cache
   */
  delete(key: string): boolean {
    return this.cache.delete(key);
  }

  /**
   * Clear all items from cache
   */
  clear(): void {
    this.cache.clear();
    this.hits = 0;
    this.misses = 0;
  }

  /**
   * Get cache statistics
   */
  getStats(): {
    name: string;
    size: number;
    maxSize: number;
    hits: number;
    misses: number;
    hitRate: number;
    calculatedSize: number;
  } {
    const total = this.hits + this.misses;
    
    return {
      name: this.name,
      size: this.cache.size,
      maxSize: this.cache.max,
      hits: this.hits,
      misses: this.misses,
      hitRate: total > 0 ? (this.hits / total) * 100 : 0,
      calculatedSize: this.cache.calculatedSize || 0
    };
  }

  /**
   * Get or set pattern - fetch data if not in cache
   */
  async getOrSet<K>(
    key: string,
    fetchFn: () => Promise<T>,
    ttlMs?: number
  ): Promise<T> {
    const cached = this.get(key);
    
    if (cached !== null) {
      return cached;
    }

    try {
      const data = await fetchFn();
      this.set(key, data, ttlMs);
      return data;
    } catch (error) {
      console.error(`[${this.name}] Error fetching data for key ${key}:`, error);
      throw error;
    }
  }

  /**
   * Peek at item without updating LRU order
   */
  peek(key: string): T | null {
    const entry = this.cache.peek(key);
    return entry ? entry.data : null;
  }

  /**
   * Get all keys in cache
   */
  keys(): string[] {
    return Array.from(this.cache.keys());
  }

  /**
   * Get cache info for debugging
   */
  getInfo(): {
    name: string;
    entries: Array<{
      key: string;
      timestamp: number;
      age: number;
    }>;
    stats: {
      hits: number;
      misses: number;
      hitRate: number;
      size: number;
      maxSize: number;
    };
  } {
    const entries: Array<{ key: string; timestamp: number; age: number }> = [];
    const now = Date.now();
    
    // Convert iterator to array to avoid downlevelIteration issues
    const cacheEntries = Array.from(this.cache.entries());
    for (const [key, entry] of cacheEntries) {
      entries.push({
        key,
        timestamp: entry.timestamp,
        age: now - entry.timestamp
      });
    }

    return {
      name: this.name,
      entries,
      stats: this.getStats()
    };
  }
}

/**
 * Pre-configured cache instances for different data types
 */
export const CacheInstances = {
  // Options chain cache - larger size, shorter TTL
  optionsChain: new QuantivLRUCache('options-chain', {
    maxSize: 200,
    ttlMs: 60 * 1000, // 1 minute
    updateAgeOnGet: true
  }),

  // Expected move cache - medium size, medium TTL
  expectedMove: new QuantivLRUCache('expected-move', {
    maxSize: 150,
    ttlMs: 90 * 1000, // 1.5 minutes
    updateAgeOnGet: true
  }),

  // Earnings data cache - smaller size, longer TTL
  earnings: new QuantivLRUCache('earnings', {
    maxSize: 100,
    ttlMs: 300 * 1000, // 5 minutes
    updateAgeOnGet: true
  }),

  // Price history cache - smaller size, longer TTL
  priceHistory: new QuantivLRUCache('price-history', {
    maxSize: 50,
    ttlMs: 600 * 1000, // 10 minutes
    updateAgeOnGet: false // Don't update age for historical data
  }),

  // IV series cache - medium size, longer TTL
  ivSeries: new QuantivLRUCache('iv-series', {
    maxSize: 100,
    ttlMs: 1800 * 1000, // 30 minutes
    updateAgeOnGet: false
  })
};

/**
 * Get all cache statistics for monitoring
 */
export function getAllCacheStats(): Record<string, ReturnType<QuantivLRUCache<any>['getStats']>> {
  return {
    optionsChain: CacheInstances.optionsChain.getStats(),
    expectedMove: CacheInstances.expectedMove.getStats(),
    earnings: CacheInstances.earnings.getStats(),
    priceHistory: CacheInstances.priceHistory.getStats(),
    ivSeries: CacheInstances.ivSeries.getStats()
  };
}

/**
 * Clear all caches (useful for testing or manual cache invalidation)
 */
export function clearAllCaches(): void {
  Object.values(CacheInstances).forEach(cache => cache.clear());
}

/**
 * Utility to generate cache keys for different data types
 */
export const CacheKeys = {
  optionsChain: (symbol: string, expiry: string) => `${symbol.toUpperCase()}:${expiry}`,
  expectedMove: (symbol: string, expiry: string) => `${symbol.toUpperCase()}:${expiry}`,
  earnings: (symbol: string) => symbol.toUpperCase(),
  priceHistory: (symbol: string, days: number) => `${symbol.toUpperCase()}:${days}d`,
  ivSeries: (symbol: string, days: number) => `${symbol.toUpperCase()}:${days}d`
};
