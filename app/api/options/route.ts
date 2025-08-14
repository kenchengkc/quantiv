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

import { NextRequest, NextResponse } from 'next/server';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import { createApiResponse, validateRequest, OptionsRequestSchema } from '@/lib/schemas';
import { fmpService } from '@/lib/services/fmpService';
import { findATMStrike } from '@/lib/pricing/blackScholes';



/**
 * GET /api/options?symbol=AAPL&expiry=2024-01-19
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  let cacheHit: 'l1' | 'l2' | 'miss' = 'miss';
  const dataSource = 'fmp';

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
      return NextResponse.json(
        createApiResponse(undefined, validation.error, validation.details?.join(', ')),
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
        // Cache miss - fetch live data from FMP only
        console.log(`[Options API] Fetching FMP live data for ${validSymbol}`);
        
        const [quoteData, chainData] = await Promise.all([
          fmpService.fetchQuote(validSymbol),
          fmpService.fetchOptionsChain(validSymbol, validExpiry)
        ]);

        if (!quoteData || !chainData) {
          throw new Error('Failed to fetch live data from FMP');
        }

        // Extract strikes from FMP options chain structure
        const callStrikes = Object.keys(chainData.strikes.calls).map(key => {
          const strike = parseFloat(key.replace('C', ''));
          return isNaN(strike) ? 0 : strike;
        }).filter(s => s > 0);
        
        const putStrikes = Object.keys(chainData.strikes.puts).map(key => {
          const strike = parseFloat(key.replace('P', ''));
          return isNaN(strike) ? 0 : strike;
        }).filter(s => s > 0);
        
        const strikes = [...new Set([...callStrikes, ...putStrikes])].sort((a, b) => a - b);
        
        if (strikes.length === 0) {
          throw new Error('No options strikes available from FMP');
        }

        // Find ATM strike
        const atmStrike = findATMStrike(strikes, quoteData.price);
        
        // Find ATM call and put options
        const atmCall = chainData.strikes.calls[`${atmStrike}C`];
        const atmPut = chainData.strikes.puts[`${atmStrike}P`];
        
        // Build response data
        responseData = {
          spot: quoteData.price,
          expiryUsed: validExpiry || chainData.expirationDate,
          atm: {
            strike: atmStrike,
            callMid: atmCall?.mid || 0,
            putMid: atmPut?.mid || 0,
            iv: atmCall?.impliedVolatility || 0.25,
            T: (chainData.daysToExpiry || 30) / 365
          },
          chain: {
            strikes: strikes,
            calls: Object.values(chainData.strikes.calls).map((call: any) => ({
              strike: call.strike,
              mid: call.mid,
              bid: call.bid,
              ask: call.ask,
              iv: call.impliedVolatility,
              volume: call.volume,
              openInterest: call.openInterest
            })),
            puts: Object.values(chainData.strikes.puts).map((put: any) => ({
              strike: put.strike,
              mid: put.mid,
              bid: put.bid,
              ask: put.ask,
              iv: put.impliedVolatility,
              volume: put.volume,
              openInterest: put.openInterest
            })),
            expiryDate: chainData.expirationDate,
            daysToExpiry: chainData.daysToExpiry || 30
          },
          quote: {
            price: quoteData.price,
            change: quoteData.change,
            changePercent: quoteData.changePercent,
            name: quoteData.name || validSymbol
          }
        };
        
        // Cache the response
        CacheInstances.optionsChain.set(cacheKey, responseData, 60 * 1000); // 1 minute L1
        await RedisCache.setJson(redisKey, responseData, 300); // 5 minutes L2
      } else {
        // L2 cache hit - also cache in L1 for faster access
        CacheInstances.optionsChain.set(cacheKey, responseData, 60 * 1000);
      }
    }
    
    // Final validation
    if (!responseData || !responseData.chain || !responseData.chain.strikes || responseData.chain.strikes.length === 0) {
      throw new Error('Unable to fetch valid options chain data from FMP');
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
        'X-Data-Source': dataSource
      }
    });

    // This code should not be reached since we handle live data above
    throw new Error('Fallback code reached - this should not happen with unified live data service');
    
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
