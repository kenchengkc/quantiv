/**
 * Options Contracts Discovery API Route
 * 
 * Provides comprehensive options contract discovery and analysis using Polygon All Contracts endpoint.
 * Enables market availability analysis, strategy development, and contract exploration.
 * 
 * Query Parameters:
 * - symbol: Stock symbol (required)
 * - type: Contract type - 'call' or 'put' (optional)
 * - expiration: Expiration date in YYYY-MM-DD format (optional)
 * - limit: Number of contracts to return, max 250 (optional, default 250)
 * 
 * Returns:
 * - success: boolean
 * - data: Contract discovery data with analysis
 * - error: Error message if any
 * - timestamp: ISO timestamp
 */

export const dynamic = 'force-dynamic';
import { NextRequest, NextResponse } from 'next/server';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import { createApiResponse } from '@/lib/schemas';
import PolygonOptionsService from '@/lib/services/polygonOptionsService';

interface ContractsResponseData {
  contracts: unknown;
  dataSource: string;
  cacheHit: 'l1' | 'l2' | 'miss';
  responseTime: number;
}
 
/**
 * GET /api/contracts?symbol=AAPL&type=call&limit=100
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  let cacheHit: 'l1' | 'l2' | 'miss' = 'miss';

  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const searchParams = url.searchParams;
    const symbol = searchParams.get('symbol')?.toUpperCase();
    const contractType = searchParams.get('type') as 'call' | 'put' | null;
    const expiration = searchParams.get('expiration');
    const limitParam = searchParams.get('limit');
    const limit = limitParam ? Math.min(parseInt(limitParam), 250) : 250;
    
    if (!symbol) {
      return NextResponse.json(
        createApiResponse(undefined, 'Missing required parameter', 'Symbol parameter is required'),
        { status: 400 }
      );
    }

    if (contractType && !['call', 'put'].includes(contractType)) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid contract type', 'Contract type must be "call" or "put"'),
        { status: 400 }
      );
    }

    // Generate cache keys
    const cacheKey = CacheKeys.optionsChain(symbol, `contracts_${contractType || 'all'}_${expiration || 'all'}_${limit}`);
    const redisKey = Keys.optionsChain(symbol, `contracts_${contractType || 'all'}_${expiration || 'all'}_${limit}`);
    
    // Try L1 cache first
    let responseData: ContractsResponseData | undefined = CacheInstances.optionsChain.get(cacheKey) as ContractsResponseData | undefined;
    cacheHit = responseData ? 'l1' : 'miss';
    
    if (!responseData) {
      // Try L2 cache
      const redisData = (await RedisCache.getJson(redisKey)) as ContractsResponseData | null;
      responseData = redisData ?? undefined;
      cacheHit = responseData ? 'l2' : 'miss';
      
      if (!responseData) {
        // Cache miss - fetch live data from Polygon All Contracts
        console.log(`[contracts-api] Cache miss for ${symbol}, fetching from Polygon All Contracts`);
        
        const polygonOptionsService = PolygonOptionsService.getInstance();
        const contractDiscovery = await polygonOptionsService.getAllContracts(
          symbol, 
          limit, 
          contractType || undefined, 
          expiration || undefined
        );
        
        if (!contractDiscovery) {
          throw new Error(`Unable to fetch contracts for ${symbol} from Polygon All Contracts API`);
        }
        
        console.log(`[contracts-api] Contract discovery received:`, {
          symbol: contractDiscovery.symbol,
          totalContracts: contractDiscovery.totalContracts,
          expirations: contractDiscovery.expirations.length,
          strikeRange: `${contractDiscovery.strikeAnalysis.minStrike}-${contractDiscovery.strikeAnalysis.maxStrike}`
        });

        responseData = {
          contracts: contractDiscovery,
          dataSource: 'polygon-contracts',
          cacheHit,
          responseTime: Date.now() - startTime
        };
        
        // Cache the response (longer TTL for contract discovery since it changes less frequently)
        CacheInstances.optionsChain.set(cacheKey, responseData, 5 * 60 * 1000); // 5 minutes L1
        await RedisCache.setJson(redisKey, responseData, 15 * 60); // 15 minutes L2
      } else {
        // L2 cache hit - also cache in L1 for faster access
        CacheInstances.optionsChain.set(cacheKey, responseData, 5 * 60 * 1000);
      }
    }
    
    // Final validation
    if (!responseData || !responseData.contracts) {
      throw new Error('Unable to fetch valid contract discovery data');
    }
      
    const response = createApiResponse(responseData);
    const processingTime = Date.now() - startTime;
      
    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=300, stale-while-revalidate=900', // 5min cache, 15min stale
        'X-Cache-Hit': cacheHit,
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': symbol,
        'X-Data-Source': responseData.dataSource || 'polygon-contracts'
      }
    });
    
  } catch (error) {
    console.error('[API] /api/contracts error:', error);
    
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
 * OPTIONS /api/contracts - CORS preflight
 */
export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
}
