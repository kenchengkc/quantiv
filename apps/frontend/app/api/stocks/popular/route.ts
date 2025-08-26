import { NextResponse } from 'next/server';
export const dynamic = 'force-dynamic';
import { getPopularStocksAsync } from '@/lib/data/stocks';

export async function GET() {
  try {
    const results = await getPopularStocksAsync();
    return NextResponse.json(
      {
        success: true,
        data: results,
        meta: { count: results.length },
        timestamp: new Date().toISOString(),
      },
      {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'public, max-age=300, stale-while-revalidate=300',
        },
      }
    );
  } catch (err) {
    console.error('[api/stocks/popular] error:', err);
    return NextResponse.json(
      {
        success: false,
        error: 'Internal server error',
        timestamp: new Date().toISOString(),
      },
      { status: 500 }
    );
  }
}
