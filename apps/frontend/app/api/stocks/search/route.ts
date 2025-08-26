import { NextRequest, NextResponse } from 'next/server';
export const dynamic = 'force-dynamic';
import { z } from 'zod';
import { searchStocksAsync } from '@/lib/data/stocks';

// Validate query params
const QuerySchema = z.object({
  q: z.string().trim().min(1, 'q is required'),
  limit: z
    .string()
    .optional()
    .transform((v) => (v ? Math.min(20, Math.max(1, parseInt(v))) : 10)),
});

export async function GET(request: NextRequest) {
  try {
    const url = new URL(request.url);
    const q = url.searchParams.get('q') ?? '';
    const limit = url.searchParams.get('limit') ?? undefined;

    const parsed = QuerySchema.safeParse({ q, limit });
    if (!parsed.success) {
      return NextResponse.json(
        {
          success: false,
          error: 'Invalid query parameters',
          details: parsed.error.errors,
          timestamp: new Date().toISOString(),
        },
        { status: 400 }
      );
    }

    const { q: query, limit: lim } = parsed.data;

    // Use dynamic S&P 500 list when available (server-only fetch), fallback to static subset
    const results = await searchStocksAsync(query, lim);

    return NextResponse.json(
      {
        success: true,
        data: results,
        meta: { query, limit: lim, count: results.length },
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
    console.error('[api/stocks/search] error:', err);
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
