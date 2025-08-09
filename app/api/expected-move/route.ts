/**
 * /api/expected-move - Expected move calculations endpoint
 * Returns expected move data using straddle and IV methods with confidence assessment
 */

import { NextRequest, NextResponse } from 'next/server';
import { ExpectedMoveRequestSchema, ExpectedMoveResponseSchema, createApiResponse, validateRequest } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys, QuantivCache } from '@/lib/cache/redis';
import { calculateExpectedMove, assessConfidence, formatExpectedMove } from '@/lib/services/expectedMove';
import { calculateIVStats, generateMockIVData } from '@/lib/services/ivStats';
import type { ChainData } from '@/lib/services/expectedMove';

/**
 * Mock options data provider (shared logic with /api/options)
 * In production, this would be extracted to a shared provider service
 */
class OptionsProvider {
  static async getChain(symbol: string, expiry?: string): Promise<ChainData> {
    // Simulate API delay
    await new Promise(resolve => setTimeout(resolve, 80));
    
    const spot = 150.00 + Math.random() * 50;
    const selectedExpiry = expiry || '2024-02-16';
    const expiryDate = new Date(selectedExpiry);
    const daysToExpiry = Math.max(1, Math.ceil((expiryDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24)));
    
    // Generate strikes around spot price
    const strikes: number[] = [];
    const baseStrike = Math.round(spot / 5) * 5;
    for (let i = -10; i <= 10; i++) {
      strikes.push(baseStrike + (i * 5));
    }
    
    // Generate mock options data with realistic pricing
    const calls = strikes.map(strike => {
      const intrinsic = Math.max(0, spot - strike);
      const timeValue = Math.random() * 3 + 0.5;
      const mid = intrinsic + timeValue;
      const spread = Math.max(0.01, mid * 0.04); // 4% spread
      
      return {
        strike,
        mid,
        bid: Math.max(0.01, mid - spread / 2),
        ask: mid + spread / 2,
        iv: 0.20 + Math.random() * 0.30,
        volume: Math.floor(Math.random() * 1000),
        openInterest: Math.floor(Math.random() * 5000)
      };
    });
    
    const puts = strikes.map(strike => {
      const intrinsic = Math.max(0, strike - spot);
      const timeValue = Math.random() * 3 + 0.5;
      const mid = intrinsic + timeValue;
      const spread = Math.max(0.01, mid * 0.04);
      
      return {
        strike,
        mid,
        bid: Math.max(0.01, mid - spread / 2),
        ask: mid + spread / 2,
        iv: 0.20 + Math.random() * 0.30,
        volume: Math.floor(Math.random() * 1000),
        openInterest: Math.floor(Math.random() * 5000)
      };
    });
    
    return {
      symbol: symbol.toUpperCase(),
      spot,
      expiryDate: selectedExpiry,
      daysToExpiry,
      strikes,
      calls,
      puts,
      timestamp: new Date().toISOString()
    };
  }
}

/**
 * GET /api/expected-move?symbol=AAPL&expiry=2024-01-19
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const params = {
      symbol: url.searchParams.get('symbol'),
      expiry: url.searchParams.get('expiry')
    };
    
    const validation = validateRequest(ExpectedMoveRequestSchema, params);
    if (!validation.success) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid request parameters', validation.details?.join(', ')),
        { status: 400 }
      );
    }
    
    const { symbol, expiry } = validation.data;
    
    // Generate cache key for expected move
    const emCacheKey = CacheKeys.expectedMove(symbol, expiry || 'default');
    
    // Try L1 cache first
    let expectedMoveData = CacheInstances.expectedMove.get(emCacheKey);
    let cacheHit = 'l1';
    
    if (!expectedMoveData) {
      // Try L2 (Redis) cache
      const redisKey = Keys.expectedMoveSnapshot(symbol, expiry || 'default');
      expectedMoveData = await RedisCache.getJson(redisKey);
      cacheHit = expectedMoveData ? 'l2' : 'miss';
      
      if (!expectedMoveData) {
        // Cache miss - calculate expected move
        
        // First, get options chain data
        const chainCacheKey = CacheKeys.optionsChain(symbol, expiry || 'default');
        let chainData = CacheInstances.optionsChain.get(chainCacheKey);
        
        if (!chainData) {
          // Try Redis for chain data
          const chainRedisKey = Keys.optionsChain(symbol, expiry || 'default');
          chainData = await RedisCache.getJson<ChainData>(chainRedisKey);
          
          if (!chainData) {
            // Fetch fresh chain data
            chainData = await OptionsProvider.getChain(symbol, expiry);
            
            // Cache chain data
            CacheInstances.optionsChain.set(chainCacheKey, chainData, 60 * 1000);
            await RedisCache.setJson(chainRedisKey, chainData, 300);
          } else {
            // Cache in L1
            CacheInstances.optionsChain.set(chainCacheKey, chainData, 60 * 1000);
          }
        }
        
        // Calculate expected move
        const expectedMove = calculateExpectedMove(chainData);
        const confidence = assessConfidence(chainData, expectedMove.atm);
        
        expectedMoveData = {
          ...expectedMove,
          confidence,
          timestamp: new Date().toISOString(),
          symbol: chainData.symbol
        };
        
        // Cache expected move in both layers
        CacheInstances.expectedMove.set(emCacheKey, expectedMoveData, 90 * 1000); // 1.5 minutes L1
        await QuantivCache.cacheExpectedMove(symbol, expiry || 'default', expectedMoveData, 180); // 3 minutes L2
        
        // Add to top movers if significant move
        if (expectedMoveData.straddle.pct > 5.0) {
          await QuantivCache.addTopMover(symbol, expectedMoveData.straddle.pct);
        }
        
        cacheHit = 'miss';
      } else {
        // Cache in L1 for next time
        CacheInstances.expectedMove.set(emCacheKey, expectedMoveData, 90 * 1000);
      }
    }
    
    // Get IV rank data (mock for now)
    const ivCacheKey = CacheKeys.ivSeries(symbol, 252); // 1 year of data
    let ivStats = CacheInstances.ivSeries.get(ivCacheKey);
    
    if (!ivStats) {
      // Generate mock IV data and calculate stats
      const mockIVData = generateMockIVData(252); // 1 year
      const currentIV = expectedMoveData.atm?.iv || 0.25;
      
      ivStats = calculateIVStats(mockIVData, currentIV);
      
      // Cache IV stats
      CacheInstances.ivSeries.set(ivCacheKey, ivStats, 1800 * 1000); // 30 minutes
      await RedisCache.setJson(Keys.ivSeries(symbol), ivStats, 1800); // 30 minutes
    }
    
    const response = createApiResponse({
      em: expectedMoveData,
      ivRank: ivStats
    });
    
    const processingTime = Date.now() - startTime;
    
    // Add performance headers
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'public, s-maxage=90, stale-while-revalidate=300',
      'X-Cache-Hit': cacheHit,
      'X-Processing-Time': `${processingTime}ms`,
      'X-Symbol': symbol,
      'X-Expiry': expectedMoveData.atm?.expiry || 'default'
    });
    
    return NextResponse.json(response, { headers });
    
  } catch (error) {
    console.error('[API] /api/expected-move error:', error);
    
    const errorResponse = createApiResponse(
      undefined,
      'Internal server error',
      error instanceof Error ? error.message : 'Unknown error',
      'Please check symbol format and try again'
    );
    
    return NextResponse.json(errorResponse, { status: 500 });
  }
}

/**
 * OPTIONS /api/expected-move - CORS preflight
 */
export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}
