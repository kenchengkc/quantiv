/**
 * /api/earnings - Earnings data endpoint
 * Returns upcoming and historical earnings with realized moves
 */

export const dynamic = 'force-dynamic';
import { NextRequest, NextResponse } from 'next/server';
import { EarningsRequestSchema, EarningsResponseSchema, createApiResponse, validateRequest } from '@/lib/schemas';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import { fetchEnhancedEarnings, isEnhancedLiveDataAvailable } from '@/lib/services/enhancedLiveDataService';

/**
 * Mock earnings data provider
 * In production, this would integrate with earnings calendar APIs
 */
class EarningsProvider {
  static async getEarningsData(symbol: string) {
    // Simulate API delay
    await new Promise(resolve => setTimeout(resolve, 120));
    
    const now = new Date();
    const nextWeek = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
    
    // Mock upcoming earnings
    const next = Math.random() > 0.3 ? {
      date: nextWeek.toISOString().split('T')[0],
      confidence: Math.random() > 0.5 ? 'confirmed' : 'estimated' as const,
      timing: Math.random() > 0.5 ? 'bmo' : 'amc' as const,
      estimate: {
        eps: Math.random() * 2 + 0.5, // $0.50 - $2.50
        revenue: Math.random() * 50 + 10 // $10B - $60B
      }
    } : undefined;
    
    // Mock historical earnings with realized moves
    const last = [];
    for (let i = 1; i <= 4; i++) {
      const earningsDate = new Date(now.getTime() - i * 90 * 24 * 60 * 60 * 1000); // Quarterly
      const priceBefore = 150 + Math.random() * 50;
      const moveDirection = Math.random() > 0.5 ? 1 : -1;
      const realizedMovePct = (Math.random() * 8 + 2) * moveDirection; // 2-10% move
      const priceAfter = priceBefore * (1 + realizedMovePct / 100);
      
      last.push({
        date: earningsDate.toISOString().split('T')[0],
        realizedMovePct: Math.round(Math.abs(realizedMovePct) * 100) / 100,
        priceChange: Math.round((priceAfter - priceBefore) * 100) / 100,
        priceBefore: Math.round(priceBefore * 100) / 100,
        priceAfter: Math.round(priceAfter * 100) / 100,
        volume: Math.floor(Math.random() * 50000000 + 10000000) // 10M-60M volume
      });
    }
    
    return {
      symbol: symbol.toUpperCase(),
      next,
      last: last.reverse(), // Most recent first
      timestamp: new Date().toISOString()
    };
  }
}

/**
 * GET /api/earnings?symbol=AAPL
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const params = {
      symbol: url.searchParams.get('symbol')
    };
    
    const validation = validateRequest(EarningsRequestSchema, params);
    if (!validation.success) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid request parameters', validation.details?.join(', ')),
        { status: 400 }
      );
    }
    
    const { symbol } = validation.data!;
    
    // Generate cache key
    const cacheKey = CacheKeys.earnings(symbol);
    
    // Try L1 cache first
    let earningsData = CacheInstances.earnings.get(cacheKey);
    let cacheHit = 'l1';
    
    if (!earningsData) {
      // Try L2 (Redis) cache
      const redisKey = Keys.earnings(symbol);
      earningsData = await RedisCache.getJson(redisKey);
      cacheHit = earningsData ? 'l2' : 'miss';
      
      if (!earningsData) {
        // Try enhanced live data first if available
        if (isEnhancedLiveDataAvailable()) {
          try {
            const enhancedEarnings = await fetchEnhancedEarnings(symbol);
            if (enhancedEarnings) {
              // Transform enhanced data to match EarningsCalendar component format
              const transformedEarnings = {
                symbol: enhancedEarnings.symbol,
                nextEarningsDate: enhancedEarnings.nextEarningsDate,
                nextEarningsTime: enhancedEarnings.nextEarningsTime,
                estimatedEPS: enhancedEarnings.estimatedEPS,
                estimatedRevenue: enhancedEarnings.estimatedRevenue,
                historicalEarnings: enhancedEarnings.historicalEarnings.map((earning) => ({
                  date: earning.date,
                  actualEPS: earning.actualEPS,
                  estimatedEPS: earning.estimatedEPS,
                  actualRevenue: earning.actualRevenue,
                  estimatedRevenue: earning.estimatedRevenue,
                  epsSurprise: earning.epsSurprise,
                  epsSurprisePercent: earning.epsSurprisePercent,
                  revenueSurprise: earning.revenueSurprise,
                  revenueSurprisePercent: earning.revenueSurprisePercent,
                  priceMovePercent: earning.priceMovePercent
                })),
                stats: {
                  avgMove: enhancedEarnings.stats.avgMove,
                  avgAbsMove: enhancedEarnings.stats.avgAbsMove,
                  beatRate: enhancedEarnings.stats.beatRate,
                  avgBeat: enhancedEarnings.stats.avgBeat,
                  revenueBeatRate: enhancedEarnings.stats.revenueBeatRate || 0.65,
                  avgRevenueBeat: enhancedEarnings.stats.avgRevenueBeat || 0
                },
                dataSource: enhancedEarnings.dataSource
              };

              // Cache the response
              CacheInstances.earnings.set(cacheKey, transformedEarnings, 120 * 1000); // 2 minutes L1
              await RedisCache.setJson(redisKey, transformedEarnings, 600); // 10 minutes L2

              return NextResponse.json(createApiResponse(transformedEarnings));
            }
          } catch (error) {
            console.error('Enhanced earnings data fetch failed:', error);
            // Continue to fallback
          }
        }

        // Cache miss - fetch from mock provider
        earningsData = await EarningsProvider.getEarningsData(symbol);
        
        // Cache in both L1 and L2
        CacheInstances.earnings.set(cacheKey, earningsData, 300 * 1000); // 5 minutes L1
        await RedisCache.setJson(redisKey, earningsData, 1800); // 30 minutes L2
        cacheHit = 'miss';
      } else {
        // Cache in L1 for next time
        CacheInstances.earnings.set(cacheKey, earningsData, 300 * 1000);
      }
    }
    
    // Transform earnings data to match EarningsCalendar component expectations
    const earnings = earningsData as any; // Type assertion for mock data
    
    // Check if we have enhanced live data (FMP/Polygon) or mock data
    if (earnings.historicalEarnings && Array.isArray(earnings.historicalEarnings)) {
      // Enhanced live data from FMP - use directly
      const transformedData = {
        nextEarningsDate: earnings.nextEarningsDate,
        nextEarningsTime: earnings.nextEarningsTime,
        estimatedEPS: earnings.estimatedEPS,
        estimatedRevenue: earnings.estimatedRevenue,
        historicalEarnings: earnings.historicalEarnings.map((earning: any) => ({
          date: earning.date,
          actualEPS: earning.actualEPS,
          estimatedEPS: earning.estimatedEPS,
          actualRevenue: earning.actualRevenue || Math.random() * 50 + 10, // Mock revenue if not available
          estimatedRevenue: earning.estimatedRevenue || Math.random() * 48 + 9.5,
          epsSurprise: earning.actualEPS - earning.estimatedEPS,
          epsSurprisePercent: earning.estimatedEPS !== 0 ? ((earning.actualEPS - earning.estimatedEPS) / Math.abs(earning.estimatedEPS)) * 100 : 0,
          revenueSurprise: (Math.random() - 0.5) * 2, // Mock revenue surprise
          revenueSurprisePercent: (Math.random() - 0.5) * 10, // Mock revenue surprise %
          priceMovePercent: earning.priceMovePercent
        })),
        stats: earnings.stats || {
          avgMove: Math.random() * 8 + 2,
          avgAbsMove: Math.random() * 8 + 2,
          beatRate: Math.random() * 0.4 + 0.4,
          avgBeat: 0,
          revenueBeatRate: Math.random() * 0.4 + 0.5,
          avgRevenueBeat: (Math.random() - 0.5) * 5
        }
      };
      return NextResponse.json(createApiResponse(transformedData));
    }
    
    // Mock data structure - transform to match EarningsCalendar
    const transformedData = {
      nextEarningsDate: earnings.next?.date,
      nextEarningsTime: earnings.next?.timing === 'bmo' ? 'BMO' as const : 
                       earnings.next?.timing === 'amc' ? 'AMC' as const : 'UNKNOWN' as const,
      estimatedEPS: earnings.next?.estimate?.eps,
      estimatedRevenue: earnings.next?.estimate?.revenue,
      historicalEarnings: earnings.last?.map((earning: any) => ({
        date: earning.date,
        actualEPS: Math.random() * 2 + 0.5, // Mock actual EPS
        estimatedEPS: Math.random() * 2 + 0.4, // Mock estimated EPS
        actualRevenue: Math.random() * 50 + 10, // Mock revenue in billions
        estimatedRevenue: Math.random() * 48 + 9.5, // Mock estimated revenue
        epsSurprise: (Math.random() - 0.5) * 0.4, // Mock EPS surprise
        epsSurprisePercent: (Math.random() - 0.5) * 20, // Mock EPS surprise %
        revenueSurprise: (Math.random() - 0.5) * 2, // Mock revenue surprise
        revenueSurprisePercent: (Math.random() - 0.5) * 10, // Mock revenue surprise %
        priceMovePercent: earning.realizedMovePct * (earning.priceChange > 0 ? 1 : -1)
      })) || [],
      stats: {
        avgMove: earnings.avgMove || Math.random() * 8 + 2, // 2-10% average move
        avgAbsMove: earnings.avgAbsMove || Math.random() * 8 + 2, // 2-10% average absolute move
        avgBeat: earnings.avgBeat || 0,
        beatRate: earnings.beatRate || (Math.random() * 0.4 + 0.4), // 40-80% beat rate
        revenueBeatRate: earnings.revenueBeatRate || (Math.random() * 0.4 + 0.5), // 50-90% revenue beat rate
        avgRevenueBeat: earnings.avgRevenueBeat || (Math.random() - 0.5) * 5 // Mock avg revenue beat %
      }
    };

    const response = createApiResponse(transformedData);
    const processingTime = Date.now() - startTime;
    
    // Add performance headers
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=1800',
      'X-Cache-Hit': cacheHit,
      'X-Processing-Time': `${processingTime}ms`,
      'X-Symbol': symbol,
      'X-Has-Next-Earnings': (earningsData as any)?.next ? 'true' : 'false'
    });
    
    return NextResponse.json(response, { headers });
    
  } catch (error) {
    console.error('[API] /api/earnings error:', error);
    
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
 * OPTIONS /api/earnings - CORS preflight
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
