/**
 * Option Contract Snapshot API Route
 * 
 * Provides comprehensive analysis of a specific options contract using Polygon Contract Snapshot endpoint.
 * Enables detailed trade evaluation, risk assessment, and strategy refinement.
 * 
 * Query Parameters:
 * - symbol: Underlying stock symbol (required)
 * - contract: Options contract ticker (required, format: O:AAPL250815C00150000)
 * 
 * Returns:
 * - success: boolean
 * - data: Detailed contract analysis with pricing, Greeks, risk assessment
 * - error: Error message if any
 * - timestamp: ISO timestamp
 */

export const dynamic = 'force-dynamic';
import { NextRequest, NextResponse } from 'next/server';
import { CacheInstances, CacheKeys } from '@/lib/cache/lru';
import { RedisCache, Keys } from '@/lib/cache/redis';
import { createApiResponse } from '@/lib/schemas';
import PolygonOptionsService from '@/lib/services/polygonOptionsService';

interface ContractResponseData {
  analysis: unknown;
  dataSource?: string;
  cacheHit: 'l1' | 'l2' | 'miss';
  responseTime: number;
}

/**
 * GET /api/contract?symbol=AAPL&contract=O:AAPL250815C00150000
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  let cacheHit: 'l1' | 'l2' | 'miss' = 'miss';

  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const searchParams = url.searchParams;
    const symbol = searchParams.get('symbol')?.toUpperCase();
    const contract = searchParams.get('contract');
    
    if (!symbol) {
      return NextResponse.json(
        createApiResponse(undefined, 'Missing required parameter', 'Symbol parameter is required'),
        { status: 400 }
      );
    }

    if (!contract) {
      return NextResponse.json(
        createApiResponse(undefined, 'Missing required parameter', 'Contract parameter is required'),
        { status: 400 }
      );
    }

    // Validate contract format (should start with O: for options)
    if (!contract.startsWith('O:')) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid contract format', 'Contract must be in format O:SYMBOL...'),
        { status: 400 }
      );
    }

    // Generate cache keys
    const cacheKey = CacheKeys.optionsChain(symbol, `contract_${contract}`);
    const redisKey = Keys.optionsChain(symbol, `contract_${contract}`);
    
    // Try L1 cache first
    let responseData: ContractResponseData | undefined = CacheInstances.optionsChain.get(cacheKey) as ContractResponseData | undefined;
    cacheHit = responseData ? 'l1' : 'miss';
    
    if (!responseData) {
      // Try L2 cache
      responseData = (await RedisCache.getJson(redisKey)) as ContractResponseData | undefined;
      cacheHit = responseData ? 'l2' : 'miss';
      
      if (!responseData) {
        // Cache miss - fetch live data from Polygon Contract Snapshot
        console.log(`[contract-api] Cache miss for ${contract}, fetching from Polygon Contract Snapshot`);
        
        const polygonOptionsService = PolygonOptionsService.getInstance();
        const contractAnalysis = await polygonOptionsService.getContractSnapshot(symbol, contract);
        
        if (!contractAnalysis) {
          throw new Error(`Unable to fetch contract snapshot for ${contract} from Polygon Contract Snapshot API`);
        }
        
        console.log(`[contract-api] Contract analysis received:`, {
          ticker: contractAnalysis.contract.ticker,
          type: contractAnalysis.contract.contractType,
          strike: contractAnalysis.contract.strikePrice,
          expiration: contractAnalysis.contract.expirationDate,
          moneyness: contractAnalysis.underlying.moneyness,
          liquidityScore: contractAnalysis.analysis.liquidityScore
        });

        responseData = {
          analysis: contractAnalysis,
          dataSource: 'polygon-contract',
          cacheHit,
          responseTime: Date.now() - startTime
        } as ContractResponseData;
        
        // Cache the response (shorter TTL for individual contracts due to frequent price changes)
        CacheInstances.optionsChain.set(cacheKey, responseData, 2 * 60 * 1000); // 2 minutes L1
        await RedisCache.setJson(redisKey, responseData, 5 * 60); // 5 minutes L2
      } else {
        // L2 cache hit - also cache in L1 for faster access
        CacheInstances.optionsChain.set(cacheKey, responseData, 2 * 60 * 1000);
      }
    }
    
    // Final validation
    if (!responseData || !responseData.analysis) {
      throw new Error('Unable to fetch valid contract analysis data');
    }
      
    const response = createApiResponse(responseData);
    const processingTime = Date.now() - startTime;
      
    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=120, stale-while-revalidate=300', // 2min cache, 5min stale
        'X-Cache-Hit': cacheHit,
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': symbol,
        'X-Contract': contract,
        'X-Data-Source': responseData.dataSource || 'polygon-contract'
      }
    });
    
  } catch (error) {
    console.error('[API] /api/contract error:', error);
    
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
 * OPTIONS /api/contract - CORS preflight
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
