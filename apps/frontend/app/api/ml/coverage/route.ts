import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { BackendProxyError, backendProxyConfigured, proxyJsonPost } from '@/lib/backendProxy';
import { NO_STORE, SYMBOL_RE } from '../_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const CoverageRequestSchema = z.object({
  symbol: z.string().trim().toUpperCase().regex(SYMBOL_RE).optional(),
  earnings_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  fresh_window_days: z.number().int().min(1).max(60).optional(),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const parsed = CoverageRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid request', details: parsed.error.flatten() },
      { status: 400, headers: NO_STORE },
    );
  }

  if (!backendProxyConfigured()) {
    return NextResponse.json(
      { error: 'backend proxy not configured' },
      { status: 503, headers: NO_STORE },
    );
  }

  try {
    const upstream = await proxyJsonPost('/api/ml/coverage', parsed.data, {
      timeoutMs: 5_000,
    });
    return NextResponse.json(upstream, { headers: NO_STORE });
  } catch (err) {
    if (err instanceof BackendProxyError) {
      return NextResponse.json(
        { error: err.message },
        { status: err.status, headers: NO_STORE },
      );
    }
    return NextResponse.json(
      { error: 'backend coverage request failed' },
      { status: 502, headers: NO_STORE },
    );
  }
}
