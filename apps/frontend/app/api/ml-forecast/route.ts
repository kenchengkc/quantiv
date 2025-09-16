/**
 * /api/ml-forecast - ML-powered expected move forecasts proxy
 * Proxies requests to the backend ML API
 */

export const dynamic = 'force-dynamic';
import { NextRequest, NextResponse } from 'next/server';

/**
 * POST /api/ml-forecast - Get ML forecasts
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { symbol } = body;

    if (!symbol) {
      return NextResponse.json(
        { error: 'Symbol is required' },
        { status: 400 }
      );
    }

    // Proxy to backend API
    const backendResponse = await fetch(`http://localhost:8000/api/expected-move`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ symbol })
    });

    if (!backendResponse.ok) {
      throw new Error(`Backend API error: ${backendResponse.statusText}`);
    }

    const data = await backendResponse.json();
    
    return NextResponse.json(data, {
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300',
      }
    });

  } catch (error) {
    console.error('[API] /api/ml-forecast error:', error);
    
    return NextResponse.json(
      { 
        error: 'Failed to fetch ML forecast',
        details: error instanceof Error ? error.message : 'Unknown error'
      },
      { status: 500 }
    );
  }
}

/**
 * OPTIONS /api/ml-forecast - CORS preflight
 */
export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}
