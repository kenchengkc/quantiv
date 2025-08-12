/**
 * /api/options - Options chain data endpoint
 * Returns normalized options chain with ATM data and pricing
 */

import { NextRequest, NextResponse } from 'next/server';
import { createApiResponse, validateRequest } from '@/lib/schemas';
import { OptionsRequestSchema } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import { findATMStrike } from '@/lib/pricing/blackScholes';
import { fetchHybridQuoteData } from '@/lib/data/sp500Service';
import { fetchEnhancedQuote, fetchEnhancedOptionsChain, isEnhancedLiveDataAvailable } from '@/lib/services/enhancedLiveDataService';
import { fetchLiveOptionsChain, isLiveDataAvailable } from '@/lib/services/liveDataService';
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
      spot,
      expiryDate: selectedExpiry,
      daysToExpiry,
      strikes,
      calls,
      puts,

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
      expiry: url.searchParams.get('expiry') || undefined
    };
    
    const validation = validateRequest(OptionsRequestSchema, params);
    if (!validation.success) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid request parameters', validation.details?.join(', ')),
        { status: 400 }
      );
    }
    
    const { symbol, expiry } = validation.data!;
    
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
    
    // Find ATM strike and data - handle different data structures
    let atmStrike: number;
    let atmCall: any;
    let atmPut: any;
    
    // Check if we have the new enhanced data structure or old structure
    if ((chainData as any).strikes && Array.isArray((chainData as any).strikes)) {
      // Enhanced live data structure
      atmStrike = findATMStrike((chainData as any).strikes, (chainData as any).spot);
      atmCall = (chainData as any).calls?.find((c: any) => c.strike === atmStrike);
      atmPut = (chainData as any).puts?.find((p: any) => p.strike === atmStrike);
    } else if ((chainData as any).calls && Array.isArray((chainData as any).calls)) {
      // Old fallback data structure - extract strikes from calls/puts
      const callStrikes = (chainData as any).calls.map((c: any) => c.strike);
      const putStrikes = (chainData as any).puts?.map((p: any) => p.strike) || [];
      const allStrikes = [...new Set([...callStrikes, ...putStrikes])];
      
      if (allStrikes.length > 0) {
        atmStrike = findATMStrike(allStrikes, (chainData as any).spot);
        atmCall = (chainData as any).calls.find((c: any) => c.strike === atmStrike);
        atmPut = (chainData as any).puts?.find((p: any) => p.strike === atmStrike);
      } else {
        console.error('[options-api] No strikes found in chain data');
        return NextResponse.json(
          createApiResponse(undefined, 'No options data', 'No strikes available in options chain'),
          { status: 500 }
        );
      }
    } else {
      console.error('[options-api] Invalid chain data structure:', Object.keys(chainData as any));
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid data structure', 'Options chain data format not recognized'),
        { status: 500 }
      );
    }
    
    if (!atmCall || !atmPut) {
      return NextResponse.json(
        createApiResponse(undefined, 'ATM options not found', 'Unable to find call and put at ATM strike'),
        { status: 500 }
      );
    }
    
    // Calculate time to expiry in years
    const T = (chainData as any).daysToExpiry / 365;
    
    // Try enhanced live data first if available
    if (isEnhancedLiveDataAvailable()) {
      try {
        console.log(`[options-api] Fetching enhanced live data for ${symbol}`);
        const [enhancedChain, enhancedQuote] = await Promise.all([
          fetchEnhancedOptionsChain(symbol, expiry),
          fetchEnhancedQuote(symbol)
        ]);

        console.log(`[options-api] Enhanced data received:`, {
          chainExists: !!enhancedChain,
          quoteExists: !!enhancedQuote,
          chainStrikes: enhancedChain?.strikes?.length || 0,
          quotePrice: enhancedQuote?.price
        });

        if (enhancedChain && enhancedQuote && enhancedChain.strikes && enhancedChain.strikes.length > 0) {
          // Build strikes data for the MiniOptionsChain component
          const strikesByExpiry: Record<string, Record<string, any>> = {};
          strikesByExpiry[enhancedChain.expirationDate] = {};
          
          enhancedChain.strikes.forEach((strike) => {
            // Add call option
            strikesByExpiry[enhancedChain.expirationDate][`${strike.strike}C`] = {
              strike: strike.strike,
              type: 'call' as const,
              bid: strike.call.bid,
              ask: strike.call.ask,
              mark: (strike.call.bid + strike.call.ask) / 2,
              volume: strike.call.volume,
              openInterest: strike.call.openInterest,
              iv: strike.call.impliedVolatility,
              delta: strike.call.delta,
              gamma: strike.call.gamma,
              theta: strike.call.theta,
              vega: strike.call.vega
            };
            
            // Add put option
            strikesByExpiry[enhancedChain.expirationDate][`${strike.strike}P`] = {
              strike: strike.strike,
              type: 'put' as const,
              bid: strike.put.bid,
              ask: strike.put.ask,
              mark: (strike.put.bid + strike.put.ask) / 2,
              volume: strike.put.volume,
              openInterest: strike.put.openInterest,
              iv: strike.put.impliedVolatility,
              delta: strike.put.delta,
              gamma: strike.put.gamma,
              theta: strike.put.theta,
              vega: strike.put.vega
            };
          });

          // Calculate days to expiry
          const expirationDate = new Date(enhancedChain.expirationDate);
          const today = new Date();
          const daysToExpiry = Math.ceil((expirationDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));

          const response = {
            chain: {
              quote: {
                symbol: enhancedQuote.symbol,
                name: enhancedQuote.symbol,
                last: enhancedQuote.price,
                change: enhancedQuote.change,
                changePercent: enhancedQuote.changePercent,
                volume: enhancedQuote.volume,
                marketCap: enhancedQuote.marketCap,
                peRatio: enhancedQuote.peRatio
              },
              expirations: [
                {
                  date: enhancedChain.expirationDate,
                  dte: daysToExpiry
                }
              ],
              strikes: strikesByExpiry
            },
            atmStrike: enhancedChain.strikes && enhancedChain.strikes.length > 0 
              ? enhancedChain.strikes.reduce((closest, strike) => 
                  Math.abs(strike.strike - enhancedQuote.price) < Math.abs(closest - enhancedQuote.price) ? strike.strike : closest, 
                  enhancedChain.strikes[0].strike
                )
              : null,
            dataSource: enhancedChain.dataSource,
            timestamp: new Date().toISOString()
          };

          // Cache the response
          CacheInstances.optionsChain.set(cacheKey, response, 60 * 1000); // 1 minute L1
          const redisKey = Keys.optionsChain(symbol, expiry || 'default');
          await RedisCache.setJson(redisKey, response, 300); // 5 minutes L2

          return NextResponse.json(createApiResponse(response));
        }
      } catch (error) {
        console.error(`[options-api] Enhanced live data fetch failed for ${symbol}:`, error);
        console.error(`[options-api] Error details:`, {
          message: error instanceof Error ? error.message : 'Unknown error',
          stack: error instanceof Error ? error.stack : undefined
        });
        // Continue to fallback
      }
    }

    // Try to fetch live options data first
    let liveOptionsData = null;
    if (isLiveDataAvailable()) {
      try {
        liveOptionsData = await fetchLiveOptionsChain(symbol);
        console.log(`[options-api] Live data ${liveOptionsData ? 'found' : 'not found'} for ${symbol}`);
      } catch (error) {
        console.warn(`[options-api] Live data fetch failed for ${symbol}:`, error);
      }
    }

    // If we have live data, use it; otherwise fall back to mock data
    if (liveOptionsData) {
      return NextResponse.json(createApiResponse({
        symbol,
        chain: {
          quote: await fetchHybridQuoteData(symbol),
          strikes: {
            [liveOptionsData.expirationDate]: liveOptionsData.strikes.reduce((acc, strike) => {
              acc[`${strike.strike}C`] = {
                strike: strike.strike,
                type: 'call' as const,
                bid: strike.call.bid,
                ask: strike.call.ask,
                last: strike.call.last,
                volume: strike.call.volume,
                openInterest: strike.call.openInterest,
                impliedVolatility: strike.call.impliedVolatility,
                delta: strike.call.delta,
                gamma: strike.call.gamma,
                theta: strike.call.theta,
                vega: strike.call.vega
              };
              acc[`${strike.strike}P`] = {
                strike: strike.strike,
                type: 'put' as const,
                bid: strike.put.bid,
                ask: strike.put.ask,
                last: strike.put.last,
                volume: strike.put.volume,
                openInterest: strike.put.openInterest,
                impliedVolatility: strike.put.impliedVolatility,
                delta: strike.put.delta,
                gamma: strike.put.gamma,
                theta: strike.put.theta,
                vega: strike.put.vega
              };
              return acc;
            }, {} as Record<string, any>)
          }
        }
      }));
    }

    // Fallback to mock data
    // Fetch enhanced quote data (real or enhanced mock)
    const quoteData = await fetchHybridQuoteData(symbol);
    
    // Build response data to match MiniOptionsChain component expectations
    const expiryDate = (chainData as any).expiryDate;
    
    // Create strikes object organized by expiration date
    const strikesByExpiry: Record<string, Record<string, any>> = {};
    strikesByExpiry[expiryDate] = {};
    
    // Build strikes data for the MiniOptionsChain component
    (chainData as any).strikes.forEach((strike: any) => {
      const call = (chainData as any).calls.find((c: any) => c.strike === strike);
      const put = (chainData as any).puts.find((p: any) => p.strike === strike);
      
      // Add call option
      if (call) {
        strikesByExpiry[expiryDate][`${strike}C`] = {
          strike: strike,
          type: 'call' as const,
          bid: call.bid,
          ask: call.ask,
          mark: call.mid,
          volume: call.volume || 0,
          openInterest: call.openInterest || 0,
          iv: call.iv,
          delta: call.delta,
          inTheMoney: strike < (chainData as any).spot
        };
      }
      
      // Add put option
      if (put) {
        strikesByExpiry[expiryDate][`${strike}P`] = {
          strike: strike,
          type: 'put' as const,
          bid: put.bid,
          ask: put.ask,
          mark: put.mid,
          volume: put.volume || 0,
          openInterest: put.openInterest || 0,
          iv: put.iv,
          delta: put.delta,
          inTheMoney: strike > (chainData as any).spot
        };
      }
    });

    const responseData = {
      chain: {
        quote: {
          last: quoteData.price,
          change: quoteData.change,
          changePercent: quoteData.changePercent,
          name: quoteData.name,
          volume: quoteData.volume,
          marketCap: quoteData.marketCap,
          pe: quoteData.pe,
          high52Week: quoteData.high52Week,
          low52Week: quoteData.low52Week
        },
        expirations: [{ date: expiryDate, dte: (chainData as any).daysToExpiry }],
        strikes: strikesByExpiry
      },
      spot: (chainData as any).spot,
      expiryUsed: expiryDate,
      atm: {
        strike: atmStrike,
        callMid: atmCall.mid,
        putMid: atmPut.mid,
        iv: atmCall.iv || atmPut.iv || 0.25, // Fallback IV
        T: (chainData as any).daysToExpiry / 365
      },
      // Keep rows for backward compatibility with other components
      rows: (chainData as any).strikes.map((strike: any) => {
        const call = (chainData as any).calls.find((c: any) => c.strike === strike);
        const put = (chainData as any).puts.find((p: any) => p.strike === strike);
        
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
      'X-Expiry': (chainData as any).expiryDate
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
