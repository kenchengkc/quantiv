/**
 * IV History API Route
 * Fetches historical implied volatility data from local SQLite database for sparkline visualization
 * Uses LocalDoltService for fast, unlimited queries without API constraints
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { localDoltService } from '@/lib/services/localDoltService';

// Request validation schema
const IVHistoryRequestSchema = z.object({
  symbol: z.string().min(1).max(10).toUpperCase(),
  days: z.string().optional().transform(val => val ? parseInt(val) : 365),
});

export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const symbol = url.searchParams.get('symbol');
    const days = url.searchParams.get('days');
    
    const validation = IVHistoryRequestSchema.safeParse({ symbol, days });
    
    if (!validation.success) {
      return NextResponse.json({
        success: false,
        error: 'Invalid request parameters',
        details: validation.error.errors,
        timestamp: new Date().toISOString(),
      }, { status: 400 });
    }

    const { symbol: validSymbol, days: validDays } = validation.data;

    // Fetch historical IV data from local SQLite database
    console.log(`[API] Fetching IV history for ${validSymbol} (${validDays} days)`);
    const ivHistory = await localDoltService.getIVHistory(validSymbol, validDays);
    
    const processingTime = Date.now() - startTime;
    
    const response = {
      success: true,
      data: ivHistory,
      metadata: {
        symbol: validSymbol,
        days: validDays,
        dataPoints: ivHistory.length,
        dateRange: ivHistory.length > 0 ? {
          from: ivHistory[0]?.date,
          to: ivHistory[ivHistory.length - 1]?.date,
        } : null,
      },
      timestamp: new Date().toISOString(),
      dataSource: 'dolt-historical',
      processingTime: `${processingTime}ms`,
    };

    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=3600, stale-while-revalidate=7200', // 1hr cache, 2hr stale
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': validSymbol,
        'X-Data-Source': 'dolt-historical',
        'X-Data-Points': ivHistory.length.toString(),
      },
    });

  } catch (error) {
    console.error('[API] IV history error:', error);
    
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
