/**
 * IV History API Route
 * Fetches historical data for sparkline visualization.
 * Now proxies to backend EM history endpoints for centralized caching and consistency.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
export const dynamic = 'force-dynamic';

// Request validation schema
const IVHistoryRequestSchema = z.object({
  symbol: z.string().min(1).max(10).toUpperCase(),
  days: z.string().optional().transform(val => val ? parseInt(val) : 365),
  exp: z.string().optional(), // ISO date YYYY-MM-DD
});

export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const symbol = url.searchParams.get('symbol');
    const days = url.searchParams.get('days');
    const exp = url.searchParams.get('exp');
    
    const validation = IVHistoryRequestSchema.safeParse({ symbol, days, exp });
    
    if (!validation.success) {
      return NextResponse.json({
        success: false,
        error: 'Invalid request parameters',
        details: validation.error.errors,
        timestamp: new Date().toISOString(),
      }, { status: 400 });
    }

    const { symbol: validSymbol, days: validDays, exp: providedExp } = validation.data;

    // Backend API base URL
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

    // 1) Resolve expiry
    let selectedExp: string | null = providedExp ?? null;

    if (!selectedExp) {
      const expResp = await fetch(`${apiUrl}/em/expiries?symbol=${validSymbol}&window=120d`, {
        headers: { 'Accept': 'application/json' },
        // cache on edge/browser; backend caches in Redis too
        next: { revalidate: 600 },
      });

      if (expResp.ok) {
        const expData = await expResp.json();
        selectedExp = Array.isArray(expData?.expiries) && expData.expiries.length > 0
          ? expData.expiries[0]
          : null;
      } else {
        const processingTime = Date.now() - startTime;
        return NextResponse.json({
          success: true,
          data: [],
          metadata: {
            symbol: validSymbol,
            days: validDays,
            dataPoints: 0,
            dateRange: null,
            selectedExp: null,
          },
          timestamp: new Date().toISOString(),
          dataSource: 'backend-em-history',
          processingTime: `${processingTime}ms`,
        }, {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Cache-Control': 'public, max-age=600, stale-while-revalidate=600',
            'X-Processing-Time': `${processingTime}ms`,
            'X-Symbol': validSymbol,
            'X-Data-Source': 'backend-em-history',
            'X-Data-Points': '0',
            'X-Exp': '',
          },
        });
      }
    }

    if (!selectedExp) {
      const processingTime = Date.now() - startTime;
      return NextResponse.json({
        success: true,
        data: [],
        metadata: {
          symbol: validSymbol,
          days: validDays,
          dataPoints: 0,
          dateRange: null,
          selectedExp: null,
        },
        timestamp: new Date().toISOString(),
        dataSource: 'backend-em-history',
        processingTime: `${processingTime}ms`,
      }, {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'public, max-age=600, stale-while-revalidate=600',
          'X-Processing-Time': `${processingTime}ms`,
          'X-Symbol': validSymbol,
          'X-Data-Source': 'backend-em-history',
          'X-Data-Points': '0',
          'X-Exp': '',
        },
      });
    }

    // 2) Fetch EM history for the selected expiry and window
    console.log(`[API] Fetching EM history for ${validSymbol} exp=${selectedExp} (${validDays}d)`);
    const histResp = await fetch(
      `${apiUrl}/em/history?symbol=${validSymbol}&exp=${selectedExp}&window=${validDays}d`,
      { headers: { 'Accept': 'application/json' }, next: { revalidate: 600 } }
    );

    if (!histResp.ok) {
      throw new Error(`Backend history request failed with status ${histResp.status}`);
    }

    const histData = await histResp.json();
    const items = Array.isArray(histData?.items) ? histData.items : [];
    // Map to existing shape: { date, iv } for sparkline compatibility
    const ivHistory = items.map((it: any) => ({
      date: it.quote_ts,
      iv: it.em_baseline ?? 0,
    }));
    
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
        selectedExp,
      },
      timestamp: new Date().toISOString(),
      dataSource: 'backend-em-history',
      processingTime: `${processingTime}ms`,
    };

    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=600, stale-while-revalidate=600',
        'X-Processing-Time': `${processingTime}ms`,
        'X-Symbol': validSymbol,
        'X-Data-Source': 'backend-em-history',
        'X-Data-Points': ivHistory.length.toString(),
        'X-Exp': selectedExp,
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
