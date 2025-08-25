/**
 * Live Expected Move API Route
 * Uses ComprehensiveLiveDataService to combine:
 * - FMP: Live quotes for current price
 * - Polygon.io: Live options chains for straddle calculations
 * - Local DB (SQLite): Historical IV data via LocalDoltService
 * 
 * NO MOCK DATA - All data from live APIs and local database
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import ComprehensiveLiveDataService from '@/lib/services/comprehensiveLiveDataService';

// Request validation schema
const ExpectedMoveRequestSchema = z.object({
  symbol: z.string().min(1).max(10).toUpperCase(),
});

export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const symbol = url.searchParams.get('symbol');
    
    const validation = ExpectedMoveRequestSchema.safeParse({ symbol });
    
    if (!validation.success) {
      return NextResponse.json({
        success: false,
        error: 'Invalid request parameters',
        details: validation.error.errors,
        timestamp: new Date().toISOString(),
        dataSource: 'validation',
      }, { status: 400 });
    }

    const { symbol: validSymbol } = validation.data;

    // Initialize comprehensive live data service
    const liveDataService = ComprehensiveLiveDataService.getInstance();
    
    // Fetch comprehensive expected move data
    console.log(`[API] Calculating live expected move for ${validSymbol}`);
    const expectedMoveData = await liveDataService.getExpectedMove(validSymbol);
    
    if (!expectedMoveData) {
      return NextResponse.json({
        success: false,
        error: `No expected move data available for ${validSymbol}`,
        timestamp: new Date().toISOString(),
        dataSource: 'comprehensive-live',
      }, { status: 404 });
    }

    const processingTime = Date.now() - startTime;
    
    const response = {
      success: true,
      data: expectedMoveData,
      timestamp: new Date().toISOString(),
      dataSource: 'comprehensive-live',
      processingTime: `${processingTime}ms`,
    };

    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=300, stale-while-revalidate=600', // 5 min cache, 10 min stale
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': validSymbol,
        'X-Data-Source': 'comprehensive-live',
      },
    });

  } catch (error) {
    console.error('[API] Live expected move error:', error);
    
    const errorResponse = {
      success: false,
      error: 'Internal server error',
      details: error instanceof Error ? error.message : 'Unknown error',
      timestamp: new Date().toISOString(),
      dataSource: 'error',
    };
    
    return NextResponse.json(errorResponse, { status: 500 });
  }
}

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
