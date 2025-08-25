/**
 * Live Options Chain API Route
 * Uses ComprehensiveLiveDataService to combine:
 * - FMP: Live quotes and earnings
 * - Polygon.io: Live options chains and real-time Greeks
 * - Local DB (SQLite): Historical IV data via LocalDoltService
 * 
 * NO MOCK DATA - All data from live APIs and local database
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import ComprehensiveLiveDataService from '@/lib/services/comprehensiveLiveDataService';
 

// Request validation schema
const OptionsRequestSchema = z.object({
  symbol: z.string().min(1).max(10).toUpperCase(),
  expiry: z.string().optional(),
});

// Response schema
const OptionsResponseSchema = z.object({
  success: z.boolean(),
  data: z.object({
    symbol: z.string(),
    quote: z.object({
      name: z.string(),
      last: z.number(),
      change: z.number(),
      changePercent: z.number(),
    }),
    expirations: z.array(z.string()),
    strikes: z.record(z.record(z.any())),
    ivStats: z.object({
      rank: z.number(),
      percentile: z.number(),
      current: z.number(),
      high52Week: z.number(),
      low52Week: z.number(),
    }),
  }).optional(),
  error: z.string().optional(),
  timestamp: z.string(),
  dataSource: z.string(),
});

export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const symbol = url.searchParams.get('symbol');
    const expiry = url.searchParams.get('expiry');
    
    const validation = OptionsRequestSchema.safeParse({
      symbol,
      expiry: expiry || undefined,
    });
    
    if (!validation.success) {
      return NextResponse.json({
        success: false,
        error: 'Invalid request parameters',
        details: validation.error.errors,
        timestamp: new Date().toISOString(),
        dataSource: 'validation',
      }, { status: 400 });
    }

    const { symbol: validSymbol, expiry: validExpiry } = validation.data;

    // Initialize comprehensive live data service
    const liveDataService = ComprehensiveLiveDataService.getInstance();
    
    // Fetch comprehensive options chain data
    console.log(`[API] Fetching live options chain for ${validSymbol}`);
    const optionsData = await liveDataService.getOptionsChain(validSymbol, validExpiry);
    
    if (!optionsData) {
      return NextResponse.json({
        success: false,
        error: `No options data available for ${validSymbol}`,
        timestamp: new Date().toISOString(),
        dataSource: 'comprehensive-live',
      }, { status: 404 });
    }

    const processingTime = Date.now() - startTime;
    
    const response = {
      success: true,
      data: optionsData,
      timestamp: new Date().toISOString(),
      dataSource: 'comprehensive-live',
      processingTime: `${processingTime}ms`,
    };

    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=30, stale-while-revalidate=60',
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': validSymbol,
        'X-Data-Source': 'comprehensive-live',
      },
    });

  } catch (error) {
    console.error('[API] Live options chain error:', error);
    
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
