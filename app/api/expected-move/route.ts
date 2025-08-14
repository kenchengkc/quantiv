/**
 * /api/expected-move - Expected move calculations endpoint
 * Returns expected move data using straddle and IV methods with confidence assessment
 */

import { NextRequest, NextResponse } from 'next/server';
import { ExpectedMoveRequestSchema, ExpectedMoveResponseSchema, createApiResponse, validateRequest } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys, QuantivCache } from '@/lib/cache/redis';
import { fmpService } from '@/lib/services/fmpService';
import { polygonService } from '@/lib/services/polygonService';
import { computeExpectedMove, assessConfidence, formatExpectedMove } from '@/lib/services/expectedMove';
import { calculateIVStats, createMockIVHistory } from '@/lib/services/ivStats';
import { sp500DataService, fetchHybridQuoteData } from '@/lib/data/sp500Service';
import { RealisticExpectedMoveCalculator } from '@/lib/services/realisticExpectedMove';
import { findATMStrike } from '@/lib/pricing/blackScholes';
import type { ChainData } from '@/lib/services/expectedMove';

/**
 * Mock options data provider (shared logic with /api/options)
 * In production, this would be extracted to a shared provider service
 */
class OptionsProvider {
  static async getChain(symbol: string, expiry?: string): Promise<ChainData> {
    // Simulate API delay
    await new Promise(resolve => setTimeout(resolve, 80));
    
    // Get stock-specific data from S&P 500 service
    const company = sp500DataService.getCompany(symbol);
    
    // Fetch live data using FMP service only
    const [quoteData, chainData] = await Promise.all([
      fmpService.fetchQuote(symbol),
polygonService.fetchOptionsChain(symbol, expiry || undefined)
    ]);

    if (!quoteData || !chainData) {
      throw new Error('Failed to fetch live data from FMP');
    }

    // Extract strikes from FMP structure and find ATM
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
      throw new Error('No strikes available in FMP options chain');
    }

    const atmStrike = findATMStrike(strikes, quoteData.price);
    const selectedExpiry = expiry || chainData.expirationDate;
    const daysToExpiry = chainData.daysToExpiry;

    // Find ATM call and put from FMP structure
    const atmCall = chainData.strikes.calls[`${atmStrike}C`];
    const atmPut = chainData.strikes.puts[`${atmStrike}P`];

    const callMid = atmCall?.mid || 0;
    const putMid = atmPut?.mid || 0;
    const iv = atmCall?.impliedVolatility || atmPut?.impliedVolatility || 0.25;

    // Generate stock-specific options data with realistic pricing
    const calls = strikes.map(strike => {
      const intrinsic = Math.max(0, quoteData.price - strike);
      const moneyness = strike / quoteData.price;
      
      // Adjust IV based on moneyness (volatility smile)
      let iv = atmCall?.impliedVolatility || atmPut?.impliedVolatility || 0.25;
      if (moneyness < 0.95 || moneyness > 1.05) {
        iv *= 1.2; // Higher IV for OTM options
      }
      
      // Calculate time value based on IV and time to expiry
      const timeValue = quoteData.price * iv * Math.sqrt(daysToExpiry / 365) * 0.4;
      const mid = intrinsic + timeValue;
      const spread = Math.max(0.01, mid * 0.03); // Tighter spreads for liquid stocks
      
      return {
        strike,
        mid,
        bid: Math.max(0.01, mid - spread / 2),
        ask: mid + spread / 2,
        iv: iv,
        volume: Math.floor(Math.random() * this.getSectorVolumeRange(company?.sector || 'Technology').max),
        openInterest: Math.floor(Math.random() * 10000)
      };
    });
    
    const puts = strikes.map(strike => {
      const intrinsic = Math.max(0, strike - quoteData.price);
      const moneyness = strike / quoteData.price;
      
      // Adjust IV based on moneyness (volatility smile)
      let putIV = atmCall?.impliedVolatility || atmPut?.impliedVolatility || 0.25;
      if (moneyness < 0.95 || moneyness > 1.05) {
        putIV *= 1.2; // Higher IV for OTM options
      }
      
      // Put-call skew: puts typically have higher IV
      putIV *= 1.05;
      
      const timeValue = quoteData.price * putIV * Math.sqrt(daysToExpiry / 365) * 0.4;
      const mid = intrinsic + timeValue;
      const spread = Math.max(0.01, mid * 0.03);
      
      return {
        strike,
        mid,
        bid: Math.max(0.01, mid - spread / 2),
        ask: mid + spread / 2,
        iv: iv,
        volume: Math.floor(Math.random() * this.getSectorVolumeRange(company?.sector || 'Technology').max),
        openInterest: Math.floor(Math.random() * 10000)
      };
    });
    
    return {
      spot: quoteData.price,
      expiryDate: selectedExpiry,
      daysToExpiry,
      strikes,
      calls,
      puts
    };
  }

  // Get sector-specific implied volatility ranges
  static getSectorIVRange(sector: string): { min: number; max: number } {
    const ivRanges: Record<string, { min: number; max: number }> = {
      'Technology': { min: 0.25, max: 0.45 }, // Higher volatility
      'Healthcare': { min: 0.20, max: 0.35 },
      'Financial Services': { min: 0.18, max: 0.30 },
      'Consumer Cyclical': { min: 0.22, max: 0.38 },
      'Communication Services': { min: 0.20, max: 0.35 },
      'Industrials': { min: 0.18, max: 0.28 },
      'Consumer Defensive': { min: 0.15, max: 0.25 }, // Lower volatility
      'Energy': { min: 0.25, max: 0.40 },
      'Utilities': { min: 0.12, max: 0.22 }, // Lowest volatility
      'Real Estate': { min: 0.18, max: 0.30 },
      'Materials': { min: 0.20, max: 0.32 }
    };
    return ivRanges[sector] || ivRanges['Technology'];
  }

  // Get sector-specific volume ranges
  static getSectorVolumeRange(sector: string): { min: number; max: number } {
    const volumeRanges: Record<string, { min: number; max: number }> = {
      'Technology': { min: 500, max: 5000 }, // High volume
      'Healthcare': { min: 200, max: 2000 },
      'Financial Services': { min: 300, max: 3000 },
      'Consumer Cyclical': { min: 100, max: 1500 },
      'Communication Services': { min: 200, max: 2500 },
      'Industrials': { min: 100, max: 1000 },
      'Consumer Defensive': { min: 50, max: 800 },
      'Energy': { min: 200, max: 2000 },
      'Utilities': { min: 50, max: 500 }, // Lowest volume
      'Real Estate': { min: 100, max: 1000 },
      'Materials': { min: 150, max: 1200 }
    };
    return volumeRanges[sector] || volumeRanges['Technology'];
  }

  // Get sector-specific price ranges
  static getSectorPriceRange(sector: string): { min: number; max: number } {
    const priceRanges: Record<string, { min: number; max: number }> = {
      'Technology': { min: 50, max: 500 }, // Wide range for tech stocks
      'Healthcare': { min: 30, max: 300 },
      'Financial Services': { min: 20, max: 200 },
      'Consumer Cyclical': { min: 25, max: 250 },
      'Communication Services': { min: 40, max: 400 },
      'Industrials': { min: 30, max: 180 },
      'Consumer Defensive': { min: 40, max: 150 },
      'Energy': { min: 20, max: 120 },
      'Utilities': { min: 60, max: 100 }, // Narrow range for utilities
      'Real Estate': { min: 15, max: 80 },
      'Materials': { min: 25, max: 150 }
    };
    return priceRanges[sector] || priceRanges['Technology'];
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
      expiry: url.searchParams.get('expiry') || undefined
    };
    
    const validation = validateRequest(ExpectedMoveRequestSchema, params);
    if (!validation.success) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid request parameters', validation.details?.join(', ')),
        { status: 400 }
      );
    }
    
    const { symbol, expiry } = validation.data!;
    
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
        // Try to fetch enhanced live data first
        let enhancedQuote = null;
        let enhancedChain = null;
        if (fmpService.isAvailable()) {
          try {
            console.log(`[expected-move-api] Fetching enhanced live data for ${symbol}`);
            [enhancedQuote, enhancedChain] = await Promise.all([
              fmpService.fetchQuote(symbol),
              polygonService.fetchOptionsChain(symbol, expiry || undefined)
            ]);
            console.log(`[expected-move-api] Enhanced data received:`, {
              quoteExists: !!enhancedQuote,
              chainExists: !!enhancedChain,
              quotePrice: enhancedQuote?.price,
              chainStrikes: enhancedChain?.strikes ? Object.keys(enhancedChain.strikes.calls).length + Object.keys(enhancedChain.strikes.puts).length : 0
            });
          } catch (error) {
            console.warn(`[expected-move-api] Enhanced live data fetch failed for ${symbol}:`, error);
          }
        }

        // If we have enhanced live data, use it to calculate realistic expected move
        if (enhancedQuote && enhancedChain && enhancedChain.strikes && (Object.keys(enhancedChain.strikes.calls).length > 0 || Object.keys(enhancedChain.strikes.puts).length > 0)) {
          console.log(`[expected-move-api] Using enhanced live data for realistic expected move calculation`);
          
          // Use realistic expected move calculator with live data
          const realisticExpectedMove = await RealisticExpectedMoveCalculator.calculateRealisticExpectedMove(symbol);
          // Use the conservative expected moves from RealisticExpectedMoveCalculator
          expectedMoveData = {
            symbol,
            summary: {
              daily: {
                move: realisticExpectedMove.summary.daily,
                percentage: (realisticExpectedMove.summary.daily / realisticExpectedMove.currentPrice) * 100,
                lower: realisticExpectedMove.currentPrice - realisticExpectedMove.summary.daily,
                upper: realisticExpectedMove.currentPrice + realisticExpectedMove.summary.daily
              },
              weekly: {
                move: realisticExpectedMove.summary.weekly,
                percentage: (realisticExpectedMove.summary.weekly / realisticExpectedMove.currentPrice) * 100,
                lower: realisticExpectedMove.currentPrice - realisticExpectedMove.summary.weekly,
                upper: realisticExpectedMove.currentPrice + realisticExpectedMove.summary.weekly
              },
              monthly: {
                move: realisticExpectedMove.summary.monthly,
                percentage: (realisticExpectedMove.summary.monthly / realisticExpectedMove.currentPrice) * 100,
                lower: realisticExpectedMove.currentPrice - realisticExpectedMove.summary.monthly,
                upper: realisticExpectedMove.currentPrice + realisticExpectedMove.summary.monthly
              }
            },
            straddle: realisticExpectedMove.straddle,
            iv: realisticExpectedMove.iv,
            timestamp: new Date().toISOString()
          };

          // Cache the enhanced data for 1 hour stability
          CacheInstances.expectedMove.set(emCacheKey, expectedMoveData, 60 * 60 * 1000); // 1 hour L1
          await RedisCache.setJson(Keys.expectedMoveSnapshot(symbol, expiry || 'default'), expectedMoveData, 3600); // 1 hour TTL for analysis stability

          const response = createApiResponse(expectedMoveData);
          return NextResponse.json(response);
        }

        // Cache miss - calculate realistic expected move
        
        // Use realistic expected move calculator based on historical data
        const realisticExpectedMove = await RealisticExpectedMoveCalculator.calculateRealisticExpectedMove(symbol);
        expectedMoveData = {
          symbol,
          summary: {
            daily: {
              move: realisticExpectedMove.straddle.move,
              percentage: realisticExpectedMove.straddle.movePercent,
              lower: realisticExpectedMove.currentPrice - realisticExpectedMove.straddle.move,
              upper: realisticExpectedMove.currentPrice + realisticExpectedMove.straddle.move
            },
            weekly: {
              move: realisticExpectedMove.straddle.move * 2.65,
              percentage: realisticExpectedMove.straddle.movePercent * 2.65,
              lower: realisticExpectedMove.currentPrice - (realisticExpectedMove.straddle.move * 2.65),
              upper: realisticExpectedMove.currentPrice + (realisticExpectedMove.straddle.move * 2.65)
            },
            monthly: {
              move: realisticExpectedMove.straddle.move * 5.48,
              percentage: realisticExpectedMove.straddle.movePercent * 5.48,
              lower: realisticExpectedMove.currentPrice - (realisticExpectedMove.straddle.move * 5.48),
              upper: realisticExpectedMove.currentPrice + (realisticExpectedMove.straddle.move * 5.48)
            }
          },
          straddle: realisticExpectedMove.straddle,
          iv: realisticExpectedMove.iv,
          timestamp: new Date().toISOString()
        };
        
        // Cache expected move in both layers for 1 hour stability
        CacheInstances.expectedMove.set(emCacheKey, expectedMoveData, 60 * 60 * 1000); // 1 hour L1
        await QuantivCache.cacheExpectedMove(symbol, expiry || 'default', expectedMoveData, 3600); // 1 hour L2
        
        // Add to top movers if significant move
        if ((expectedMoveData as any)?.straddle?.pct > 5.0) {
          await QuantivCache.addTopMover(symbol, (expectedMoveData as any).straddle.pct);
        }
        
        cacheHit = 'miss';
      } else {
        // Cache in L1 for next time with 1 hour stability
        CacheInstances.expectedMove.set(emCacheKey, expectedMoveData, 60 * 60 * 1000); // 1 hour L1
      }
    }
    
    // Get IV rank data (mock for now)
    const ivCacheKey = CacheKeys.ivSeries(symbol, 252); // 1 year of data
    let ivStats = CacheInstances.ivSeries.get(ivCacheKey);
    
    if (!ivStats) {
      // Generate mock IV data and calculate stats
      const ivHistory = createMockIVHistory(252, 0.25); // 1 year
      const currentIV = (expectedMoveData as any)?.atm?.iv || 0.25;
      
      ivStats = calculateIVStats(ivHistory, currentIV);
      
      // Cache IV stats
      CacheInstances.ivSeries.set(ivCacheKey, ivStats, 1800 * 1000); // 30 minutes
      await RedisCache.setJson(Keys.ivSeries(symbol), ivStats, 1800); // 30 minutes
    }
    
    // Transform data to match ExpectedMoveCard component expectations
    const spotPrice = (expectedMoveData as any)?.atm?.spot || 150;
    const straddleMove = (expectedMoveData as any)?.straddle;
    
    const response = createApiResponse({
      symbol: symbol,
      spotPrice: spotPrice,
      summary: {
        daily: straddleMove ? {
          move: straddleMove.abs,
          percentage: straddleMove.pct,
          lower: spotPrice - straddleMove.abs,
          upper: spotPrice + straddleMove.abs
        } : null,
        weekly: straddleMove ? {
          move: straddleMove.abs * 2.65, // Approximate weekly scaling
          percentage: straddleMove.pct * 2.65,
          lower: spotPrice - (straddleMove.abs * 2.65),
          upper: spotPrice + (straddleMove.abs * 2.65)
        } : null,
        monthly: straddleMove ? {
          move: straddleMove.abs * 5.48, // Approximate monthly scaling (sqrt(30))
          percentage: straddleMove.pct * 5.48,
          lower: spotPrice - (straddleMove.abs * 5.48),
          upper: spotPrice + (straddleMove.abs * 5.48)
        } : null
      },
      // Also include the raw data for other components that might need it
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
      'X-Expiry': (expectedMoveData as any)?.atm?.expiry || 'default'
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
