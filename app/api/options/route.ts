/**
 * /api/options - Options chain data endpoint
 * Returns normalized options chain with ATM data and pricing
 */

import { NextRequest, NextResponse } from 'next/server';
import { OptionsRequestSchema, OptionsResponseSchema, createApiResponse, validateRequest } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import { findATMStrike } from '@/lib/pricing/blackScholes';
import type { ChainData } from '@/lib/services/expectedMove';

/**
 * Mock options data provider
 * In production, this would integrate with Yahoo Finance, CBOE, or other data sources
 */
class OptionsProvider {
  static async getChain(symbol: string, expiry?: string): Promise<ChainData> {
    // Simulate API delay
    await new Promise(resolve => setTimeout(resolve, 100));
    
    // Mock data for testing - in production this would fetch from external API
    const spot = 150.00 + Math.random() * 50; // Random spot price
    const selectedExpiry = expiry || '2024-02-16';
    const expiryDate = new Date(selectedExpiry);
    const daysToExpiry = Math.max(1, Math.ceil((expiryDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24)));
    
    // Generate strikes around spot price
    const strikes: number[] = [];
    const baseStrike = Math.round(spot / 5) * 5; // Round to nearest $5
    for (let i = -10; i <= 10; i++) {
      strikes.push(baseStrike + (i * 5));
    }
    
    // Generate mock options data
    const calls = strikes.map(strike => ({
      strike,
      mid: Math.max(0.01, spot - strike + Math.random() * 5),
      bid: 0,
      ask: 0,
      iv: 0.20 + Math.random() * 0.30, // 20-50% IV
      volume: Math.floor(Math.random() * 1000),
      openInterest: Math.floor(Math.random() * 5000)
    }));
    
    // Fix bid/ask based on mid
    calls.forEach(call => {
      const spread = Math.max(0.01, call.mid * 0.05); // 5% spread
      call.bid = Math.max(0.01, call.mid - spread / 2);
      call.ask = call.mid + spread / 2;
    });
    
    const puts = strikes.map(strike => ({
      strike,
      mid: Math.max(0.01, strike - spot + Math.random() * 5),
      bid: 0,
      ask: 0,
      iv: 0.20 + Math.random() * 0.30,
      volume: Math.floor(Math.random() * 1000),
      openInterest: Math.floor(Math.random() * 5000)
    }));
    
    // Fix bid/ask for puts
    puts.forEach(put => {
      const spread = Math.max(0.01, put.mid * 0.05);
      put.bid = Math.max(0.01, put.mid - spread / 2);
      put.ask = put.mid + spread / 2;
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
 * GET /api/options?symbol=AAPL&expiry=2024-01-19
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
    
    const validation = validateRequest(OptionsRequestSchema, params);
    if (!validation.success) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid request parameters', validation.details?.join(', ')),
        { status: 400 }
      );
    }
    
    const { symbol, expiry } = validation.data;
    
    // Generate cache key
    const cacheKey = CacheKeys.optionsChain(symbol, expiry || 'default');
    
    // Try L1 cache first
    let chainData = CacheInstances.optionsChain.get(cacheKey);
    let cacheHit = 'l1';
    
    if (!chainData) {
      // Try L2 (Redis) cache
      const redisKey = Keys.optionsChain(symbol, expiry || 'default');
      chainData = await RedisCache.getJson<ChainData>(redisKey);
      cacheHit = chainData ? 'l2' : 'miss';
      
      if (!chainData) {
        // Cache miss - fetch from provider
        chainData = await OptionsProvider.getChain(symbol, expiry);
        
        // Cache in both L1 and L2
        CacheInstances.optionsChain.set(cacheKey, chainData, 60 * 1000); // 1 minute L1
        await RedisCache.setJson(redisKey, chainData, 300); // 5 minutes L2
        cacheHit = 'miss';
      } else {
        // Cache in L1 for next time
        CacheInstances.optionsChain.set(cacheKey, chainData, 60 * 1000);
      }
    }
    
    // Find ATM strike and data
    const atmStrike = findATMStrike(chainData.spot, chainData.strikes);
    const atmCall = chainData.calls.find(c => c.strike === atmStrike);
    const atmPut = chainData.puts.find(p => p.strike === atmStrike);
    
    if (!atmCall || !atmPut) {
      return NextResponse.json(
        createApiResponse(undefined, 'ATM options not found', 'Unable to find call and put at ATM strike'),
        { status: 500 }
      );
    }
    
    // Calculate time to expiry in years
    const T = chainData.daysToExpiry / 365;
    
    // Build response data
    const responseData = {
      spot: chainData.spot,
      expiryUsed: chainData.expiryDate,
      atm: {
        strike: atmStrike,
        callMid: atmCall.mid,
        putMid: atmPut.mid,
        iv: atmCall.iv || atmPut.iv || 0.25, // Fallback IV
        T
      },
      rows: chainData.strikes.map(strike => {
        const call = chainData.calls.find(c => c.strike === strike);
        const put = chainData.puts.find(p => p.strike === strike);
        
        return {
          strike,
          call: call || {
            strike,
            mid: 0,
            bid: 0,
            ask: 0,
            iv: 0.25,
            volume: 0,
            openInterest: 0
          },
          put: put || {
            strike,
            mid: 0,
            bid: 0,
            ask: 0,
            iv: 0.25,
            volume: 0,
            openInterest: 0
          }
        };
      })
    };
    
    const response = createApiResponse(responseData);
    const processingTime = Date.now() - startTime;
    
    // Add performance headers
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300',
      'X-Cache-Hit': cacheHit,
      'X-Processing-Time': `${processingTime}ms`,
      'X-Symbol': symbol,
      'X-Expiry': chainData.expiryDate
    });
    
    return NextResponse.json(response, { headers });
    
  } catch (error) {
    console.error('[API] /api/options error:', error);
    
    const errorResponse = createApiResponse(
      undefined,
      'Internal server error',
      error instanceof Error ? error.message : 'Unknown error',
      'Please try again or contact support if the issue persists'
    );
    
    return NextResponse.json(errorResponse, { status: 500 });
  }
}

/**
 * OPTIONS /api/options - CORS preflight
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
