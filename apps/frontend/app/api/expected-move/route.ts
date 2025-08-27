/**
 * /api/expected-move - Expected move calculations endpoint
 * Returns expected move data using straddle and IV methods with confidence assessment
 */

export const dynamic = 'force-dynamic';
import { NextRequest, NextResponse } from 'next/server';
import { ExpectedMoveRequestSchema, createApiResponse, validateRequest } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys, QuantivCache } from '@/lib/cache/redis';
import { fmpService } from '@/lib/services/fmpService';
import { polygonService } from '@/lib/services/polygonService';
import { RealisticExpectedMoveCalculator } from '@/lib/services/realisticExpectedMove';

interface ExpectedMoveAtm { iv?: number; spot?: number; expiry?: string }
interface ExpectedMoveStraddle { abs?: number; pct?: number; move?: number; movePercent?: number }
interface ExpectedMoveSummaryBucket { move?: number; percentage?: number; lower?: number; upper?: number }
interface ExpectedMoveData {
  atm?: ExpectedMoveAtm;
  straddle?: ExpectedMoveStraddle;
  iv?: unknown;
  summary?: {
    daily?: ExpectedMoveSummaryBucket;
    weekly?: ExpectedMoveSummaryBucket;
    monthly?: ExpectedMoveSummaryBucket;
  };
  symbol?: string;
  timestamp?: string;
}

/** Removed unused OptionsProvider mock provider to satisfy lint */

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
    let expectedMoveData: ExpectedMoveData | undefined = CacheInstances.expectedMove.get(emCacheKey) as ExpectedMoveData | undefined;
    let cacheHit = 'l1';
    
    if (!expectedMoveData) {
      // Try L2 (Redis) cache
      const redisKey = Keys.expectedMoveSnapshot(symbol, expiry || 'default');
      const redisData = (await RedisCache.getJson(redisKey)) as ExpectedMoveData | null;
      expectedMoveData = redisData ?? undefined;
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
        const pct = expectedMoveData?.straddle?.pct ?? expectedMoveData?.straddle?.movePercent ?? 0;
        if (pct > 5.0) {
          await QuantivCache.addTopMover(symbol, pct);
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
      // No mock data - IV stats require historical IV data from live APIs
      // Set default IV stats when historical data is not available
      const currentIV = expectedMoveData?.atm?.iv ?? 0.25;
      
      ivStats = {
        rank: 0, // Would need historical IV data to calculate
        percentile: 0, // Would need historical IV data to calculate
        current: currentIV * 100,
        high52Week: currentIV * 150,
        low52Week: currentIV * 50
      };
      
      // Cache IV stats
      CacheInstances.ivSeries.set(ivCacheKey, ivStats, 1800 * 1000); // 30 minutes
      await RedisCache.setJson(Keys.ivSeries(symbol), ivStats, 1800); // 30 minutes
    }
    
    // Transform data to match ExpectedMoveCard component expectations
    const spotPrice = expectedMoveData?.atm?.spot ?? 150;
    const straddleMove = expectedMoveData?.straddle;
    
    const response = createApiResponse({
      symbol: symbol,
      spotPrice: spotPrice,
      summary: {
        daily: straddleMove ? {
          move: straddleMove.abs ?? 0,
          percentage: straddleMove.pct ?? 0,
          lower: spotPrice - (straddleMove.abs ?? 0),
          upper: spotPrice + (straddleMove.abs ?? 0)
        } : null,
        weekly: straddleMove ? {
          move: (straddleMove.abs ?? 0) * 2.65, // Approximate weekly scaling
          percentage: (straddleMove.pct ?? 0) * 2.65,
          lower: spotPrice - ((straddleMove.abs ?? 0) * 2.65),
          upper: spotPrice + ((straddleMove.abs ?? 0) * 2.65)
        } : null,
        monthly: straddleMove ? {
          move: (straddleMove.abs ?? 0) * 5.48, // Approximate monthly scaling (sqrt(30))
          percentage: (straddleMove.pct ?? 0) * 5.48,
          lower: spotPrice - ((straddleMove.abs ?? 0) * 5.48),
          upper: spotPrice + ((straddleMove.abs ?? 0) * 5.48)
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
      'X-Expiry': expectedMoveData?.atm?.expiry ?? 'default'
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
