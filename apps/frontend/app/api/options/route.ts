/**
 * Options Chain API Route
 * 
 * Provides options chain data with caching and validation.
 * Uses ONLY FMP live data - NO mock data fallbacks.
 * 
 * Query Parameters:
 * - symbol: Stock symbol (required)
 * - expiry: Expiration date in YYYY-MM-DD format (optional)
 * 
 * Returns:
 * - success: boolean
 * - data: Options chain data with ATM strike info
 * - error: Error message if any
 * - timestamp: ISO timestamp
 */
export const dynamic = 'force-dynamic';

import { NextRequest, NextResponse } from 'next/server';
import { validateRequest } from '@/lib/schemas';
import { OptionsRequestSchema } from '@/lib/schemas';
import { createApiResponse } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import PolygonOptionsService from '@/lib/services/polygonOptionsService';
import { findATMStrike } from '@/lib/pricing/blackScholes';



/**
 * GET /api/options?symbol=AAPL&expiry=2024-01-19
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  let cacheHit: 'l1' | 'l2' | 'miss' = 'miss';
  const dataSource = 'fmp+polygon';

  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const searchParams = url.searchParams;
    const symbol = searchParams.get('symbol');
    const expiry = searchParams.get('expiry');
    
    const validation = validateRequest(OptionsRequestSchema, {
      symbol,
      expiry
    });
    
    if (!validation.success) {
      console.error('[API] /api/options validation failed:', {
        error: validation.error,
        details: validation.details,
        url: request.url,
        searchParams: Object.fromEntries(searchParams),
        receivedData: { symbol, expiry }
      });
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid request', validation.error || 'Validation failed'),
        { status: 400 }
      );
    }

    const { symbol: validSymbol, expiry: validExpiry } = validation.data!;

    // Generate cache keys
    const cacheKey = CacheKeys.optionsChain(validSymbol, validExpiry || 'default');
    const redisKey = Keys.optionsChain(validSymbol, validExpiry || 'default');
    
    // Try L1 cache first
    let responseData: any = CacheInstances.optionsChain.get(cacheKey);
    cacheHit = responseData ? 'l1' : 'miss';
    
    if (!responseData) {
      // Try L2 cache
      responseData = await RedisCache.getJson(redisKey);
      cacheHit = responseData ? 'l2' : 'miss';
      
      if (!responseData) {
        // Cache miss - fetch live data using enhanced Polygon Options Chain Snapshot
        console.log(`[options-api] Cache miss for ${validSymbol}, fetching from Polygon Options Chain Snapshot`);
        
        const polygonOptionsService = PolygonOptionsService.getInstance();
        const enhancedChain = await polygonOptionsService.getOptionsChain(validSymbol, validExpiry || undefined);
        
        if (!enhancedChain) {
          throw new Error(`Unable to fetch options chain for ${validSymbol} from Polygon Options Chain Snapshot API`);
        }
        
        console.log(`[options-api] Enhanced Polygon chain received:`, {
          symbol: enhancedChain.symbol,
          underlyingPrice: enhancedChain.underlyingPrice,
          expirations: enhancedChain.expirations.length,
          totalStrikes: enhancedChain.expirations.reduce((sum, exp) => sum + exp.strikes.length, 0)
        });

        // Process enhanced Polygon options chain data
        const firstExpiration = enhancedChain.expirations[0];
        if (!firstExpiration) {
          throw new Error('No options expirations available from Polygon');
        }
        
        const strikes = firstExpiration.strikes.map(s => s.strike).sort((a, b) => a - b);
        const atmStrike = findATMStrike(strikes, enhancedChain.underlyingPrice);
        
        // Find ATM contracts for expected move calculations
        const atmStrikeData = firstExpiration.strikes.find(s => s.strike === atmStrike);
        const atmCall = atmStrikeData?.call;
        const atmPut = atmStrikeData?.put;
        
        // Build response data in enhanced format with Greeks and real-time data
        const expiryDate = validExpiry || firstExpiration.date;
        const strikesForExpiry: Record<string, any> = {};
        
        // Organize strikes with enhanced Polygon data
        firstExpiration.strikes.forEach(strikeData => {
          const { strike, call, put } = strikeData;
          
          if (call) {
            strikesForExpiry[`${strike}_call`] = {
              strike: strike,
              type: 'call' as const,
              bid: call.bid,
              ask: call.ask,
              mark: (call.bid + call.ask) / 2,
              last: call.last,
              volume: call.volume,
              openInterest: call.openInterest,
              iv: call.impliedVolatility,
              delta: call.delta,
              gamma: call.gamma,
              theta: call.theta,
              vega: call.vega,
              breakEven: call.breakEven,
              fmv: call.fmv,
              intrinsicValue: call.intrinsicValue,
              timeValue: call.timeValue,
              inTheMoney: enhancedChain.underlyingPrice > strike
            };
          }
          
          if (put) {
            strikesForExpiry[`${strike}_put`] = {
              strike: strike,
              type: 'put' as const,
              bid: put.bid,
              ask: put.ask,
              mark: (put.bid + put.ask) / 2,
              last: put.last,
              volume: put.volume,
              openInterest: put.openInterest,
              iv: put.impliedVolatility,
              delta: put.delta,
              gamma: put.gamma,
              theta: put.theta,
              vega: put.vega,
              breakEven: put.breakEven,
              fmv: put.fmv,
              intrinsicValue: put.intrinsicValue,
              timeValue: put.timeValue,
              inTheMoney: enhancedChain.underlyingPrice < strike
            };
          }
        });
        
        responseData = {
          chain: {
            quote: {
              symbol: validSymbol,
              name: validSymbol,
              last: enhancedChain.underlyingPrice,
              change: 0, // TODO: Calculate from underlying asset data
              changePercent: 0 // TODO: Calculate from underlying asset data
            },
            expirations: {
              [expiryDate]: strikesForExpiry
            },
            atmStrike: atmStrike,
            atmCall: atmCall ? {
              bid: atmCall.bid,
              ask: atmCall.ask,
              mid: (atmCall.bid + atmCall.ask) / 2,
              last: atmCall.last,
              volume: atmCall.volume,
              openInterest: atmCall.openInterest,
              impliedVolatility: atmCall.impliedVolatility,
              delta: atmCall.delta,
              gamma: atmCall.gamma,
              theta: atmCall.theta,
              vega: atmCall.vega,
              breakEven: atmCall.breakEven,
              fmv: atmCall.fmv
            } : null,
            atmPut: atmPut ? {
              bid: atmPut.bid,
              ask: atmPut.ask,
              mid: (atmPut.bid + atmPut.ask) / 2,
              last: atmPut.last,
              volume: atmPut.volume,
              openInterest: atmPut.openInterest,
              impliedVolatility: atmPut.impliedVolatility,
              delta: atmPut.delta,
              gamma: atmPut.gamma,
              theta: atmPut.theta,
              vega: atmPut.vega,
              breakEven: atmPut.breakEven,
              fmv: atmPut.fmv
            } : null
          },
          dataSource: 'polygon-enhanced',
          cacheHit,
          responseTime: Date.now() - startTime
        };
        
        // Cache the enhanced response
        CacheInstances.optionsChain.set(cacheKey, responseData, 60 * 1000); // 1 minute L1
        await RedisCache.setJson(redisKey, responseData, 300); // 5 minutes L2
      } else {
        // L2 cache hit - also cache in L1 for faster access
        CacheInstances.optionsChain.set(cacheKey, responseData, 60 * 1000);
      }
    }
    
    // Final validation
    if (!responseData || !responseData.chain) {
      throw new Error('Unable to fetch valid options chain data');
    }
      
    const response = createApiResponse(responseData);
    const processingTime = Date.now() - startTime;
      
    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=60, stale-while-revalidate=300',
        'X-Cache-Hit': cacheHit,
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': validSymbol,
        'X-Data-Source': responseData.dataSource || 'polygon-enhanced'
      }
    });

    // This code should not be reached since we handle live data above
    throw new Error('Fallback code reached - this should not happen with enhanced Polygon options service');
    
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
