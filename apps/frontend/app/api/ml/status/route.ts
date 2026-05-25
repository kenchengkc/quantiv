import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { BackendProxyError, backendProxyConfigured, proxyJsonPost } from '@/lib/backendProxy';
import { NO_STORE } from '../_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const StatusRequestSchema = z.object({
  fresh_window_days: z.number().int().min(1).max(60).optional(),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const parsed = StatusRequestSchema.safeParse(body);
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
    const upstream = await proxyJsonPost('/api/ml/status', parsed.data, {
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
      { error: 'backend status request failed' },
      { status: 502, headers: NO_STORE },
    );
  }
}
