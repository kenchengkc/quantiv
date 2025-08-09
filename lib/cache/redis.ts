/**
 * Redis Client Wrapper for Upstash
 * Provides caching utilities and key builders for Quantiv
 */

import { Redis } from '@upstash/redis';

// Initialize Redis client
const redis = new Redis({
  url: process.env.REDIS_URL!,
  token: process.env.REDIS_TOKEN!,
});

/**
 * Redis key builders following Quantiv's naming convention
 */
export const Keys = {
  // Expected move snapshot: em:snap:${symbol}:${expiry}
  expectedMoveSnapshot: (symbol: string, expiry: string) => 
    `em:snap:${symbol.toUpperCase()}:${expiry}`,
  
  // Top movers for a date: em:top:${YYYYMMDD}
  topMovers: (date: string) => 
    `em:top:${date}`,
  
  // IV series for a symbol: iv:series:${symbol}
  ivSeries: (symbol: string) => 
    `iv:series:${symbol.toUpperCase()}`,
  
  // Daily visitor count: d:visits:${YYYYMMDD}
  dailyVisits: (date: string) => 
    `d:visits:${date}`,
  
  // Options chain cache: chain:${symbol}:${expiry}
  optionsChain: (symbol: string, expiry: string) => 
    `chain:${symbol.toUpperCase()}:${expiry}`,
  
  // Earnings data: earnings:${symbol}
  earnings: (symbol: string) => 
    `earnings:${symbol.toUpperCase()}`,
  
  // Price history: prices:${symbol}
  priceHistory: (symbol: string) => 
    `prices:${symbol.toUpperCase()}`,
};

/**
 * Generic JSON cache operations
 */
export class RedisCache {
  /**
   * Set JSON data with TTL
   */
  static async setJson<T>(key: string, data: T, ttlSeconds: number = 120): Promise<void> {
    try {
      await redis.setex(key, ttlSeconds, JSON.stringify(data));
    } catch (error) {
      console.error(`Redis setJson error for key ${key}:`, error);
      // Don't throw - degrade gracefully without cache
    }
  }

  /**
   * Get JSON data
   */
  static async getJson<T>(key: string): Promise<T | null> {
    try {
      const result = await redis.get(key);
      return result ? JSON.parse(result as string) : null;
    } catch (error) {
      console.error(`Redis getJson error for key ${key}:`, error);
      return null; // Degrade gracefully
    }
  }

  /**
   * Increment counter and return new value
   */
  static async increment(key: string, ttlSeconds?: number): Promise<number> {
    try {
      const newValue = await redis.incr(key);
      
      if (ttlSeconds && newValue === 1) {
        // Set TTL only on first increment (when key is created)
        await redis.expire(key, ttlSeconds);
      }
      
      return newValue;
    } catch (error) {
      console.error(`Redis increment error for key ${key}:`, error);
      return 0; // Return 0 on error
    }
  }

  /**
   * Add to sorted set (for top movers)
   */
  static async addToSortedSet(key: string, score: number, member: string, ttlSeconds?: number): Promise<void> {
    try {
      await redis.zadd(key, { score, member });
      
      if (ttlSeconds) {
        await redis.expire(key, ttlSeconds);
      }
    } catch (error) {
      console.error(`Redis zadd error for key ${key}:`, error);
      // Don't throw - degrade gracefully
    }
  }

  /**
   * Get top N members from sorted set (descending order)
   */
  static async getTopFromSortedSet(key: string, count: number = 10): Promise<Array<{
    member: string;
    score: number;
  }>> {
    try {
      const result = await redis.zrange(key, 0, count - 1, { 
        rev: true, 
        withScores: true 
      });
      
      // Convert flat array to objects
      const items: Array<{ member: string; score: number }> = [];
      for (let i = 0; i < result.length; i += 2) {
        items.push({
          member: result[i] as string,
          score: result[i + 1] as number
        });
      }
      
      return items;
    } catch (error) {
      console.error(`Redis zrange error for key ${key}:`, error);
      return []; // Return empty array on error
    }
  }

  /**
   * Delete a key
   */
  static async delete(key: string): Promise<void> {
    try {
      await redis.del(key);
    } catch (error) {
      console.error(`Redis delete error for key ${key}:`, error);
      // Don't throw - degrade gracefully
    }
  }

  /**
   * Check if key exists
   */
  static async exists(key: string): Promise<boolean> {
    try {
      const result = await redis.exists(key);
      return result === 1;
    } catch (error) {
      console.error(`Redis exists error for key ${key}:`, error);
      return false;
    }
  }

  /**
   * Get TTL for a key
   */
  static async getTTL(key: string): Promise<number> {
    try {
      return await redis.ttl(key);
    } catch (error) {
      console.error(`Redis TTL error for key ${key}:`, error);
      return -1;
    }
  }
}

/**
 * Specialized cache operations for Quantiv
 */
export class QuantivCache {
  /**
   * Cache expected move snapshot
   */
  static async cacheExpectedMove(
    symbol: string, 
    expiry: string, 
    data: any, 
    ttlSeconds: number = 120
  ): Promise<void> {
    const key = Keys.expectedMoveSnapshot(symbol, expiry);
    const snapshot = {
      ...data,
      timestamp: new Date().toISOString(),
      symbol: symbol.toUpperCase(),
      expiry
    };
    
    await RedisCache.setJson(key, snapshot, ttlSeconds);
  }

  /**
   * Get cached expected move
   */
  static async getExpectedMove(symbol: string, expiry: string): Promise<any | null> {
    const key = Keys.expectedMoveSnapshot(symbol, expiry);
    return await RedisCache.getJson(key);
  }

  /**
   * Increment daily visitor count
   */
  static async incrementVisitorCount(): Promise<number> {
    const today = new Date().toISOString().split('T')[0].replace(/-/g, '');
    const key = Keys.dailyVisits(today);
    
    // Set TTL to 48 hours (keep yesterday's count available)
    return await RedisCache.increment(key, 48 * 60 * 60);
  }

  /**
   * Get daily visitor count
   */
  static async getVisitorCount(date?: string): Promise<number> {
    const targetDate = date || new Date().toISOString().split('T')[0].replace(/-/g, '');
    const key = Keys.dailyVisits(targetDate);
    
    try {
      const count = await redis.get(key);
      return count ? parseInt(count as string, 10) : 0;
    } catch (error) {
      console.error(`Error getting visitor count for ${targetDate}:`, error);
      return 0;
    }
  }

  /**
   * Cache IV series data
   */
  static async cacheIVSeries(symbol: string, data: any[], ttlSeconds: number = 24 * 60 * 60): Promise<void> {
    const key = Keys.ivSeries(symbol);
    await RedisCache.setJson(key, data, ttlSeconds);
  }

  /**
   * Get cached IV series
   */
  static async getIVSeries(symbol: string): Promise<any[] | null> {
    const key = Keys.ivSeries(symbol);
    return await RedisCache.getJson(key);
  }

  /**
   * Add symbol to top movers for a date
   */
  static async addTopMover(
    symbol: string, 
    expectedMovePct: number, 
    date?: string
  ): Promise<void> {
    const targetDate = date || new Date().toISOString().split('T')[0].replace(/-/g, '');
    const key = Keys.topMovers(targetDate);
    
    await RedisCache.addToSortedSet(
      key, 
      expectedMovePct, 
      symbol.toUpperCase(), 
      24 * 60 * 60 // 24 hour TTL
    );
  }

  /**
   * Get top movers for a date
   */
  static async getTopMovers(date?: string, count: number = 10): Promise<Array<{
    symbol: string;
    expectedMovePct: number;
  }>> {
    const targetDate = date || new Date().toISOString().split('T')[0].replace(/-/g, '');
    const key = Keys.topMovers(targetDate);
    
    const result = await RedisCache.getTopFromSortedSet(key, count);
    
    return result.map(item => ({
      symbol: item.member,
      expectedMovePct: item.score
    }));
  }
}

/**
 * Health check for Redis connection
 */
export async function checkRedisHealth(): Promise<{
  connected: boolean;
  latency?: number;
  error?: string;
}> {
  try {
    const start = Date.now();
    await redis.ping();
    const latency = Date.now() - start;
    
    return {
      connected: true,
      latency
    };
  } catch (error) {
    return {
      connected: false,
      error: error instanceof Error ? error.message : 'Unknown error'
    };
  }
}

/**
 * Utility to format date for Redis keys
 */
export function formatDateForKey(date: Date = new Date()): string {
  return date.toISOString().split('T')[0].replace(/-/g, '');
}

export default redis;
