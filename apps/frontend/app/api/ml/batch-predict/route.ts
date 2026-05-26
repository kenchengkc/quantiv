import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { BackendProxyError, backendProxyConfigured, proxyJsonPost } from '@/lib/backendProxy';
import {
  NO_STORE,
  PredictRequestSchema,
  type PredictRequestBody,
  buildNightlyFallbackPayload,
  loadSymbolJson,
} from '../_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const BatchPredictRequestSchema = z.object({
  items: z.array(PredictRequestSchema).min(1).max(100),
  allow_partial: z.boolean().optional().default(true),
});

function buildFallbackItem(
  item: PredictRequestBody,
  fallbackReason: string,
) {
  const response = buildNightlyFallbackPayload(
    item,
    loadSymbolJson(item.symbol),
    fallbackReason,
  );
  if (!response) {
    return {
      ok: false,
      symbol: item.symbol,
      horizon_days: item.horizon_days,
      earnings_date: item.earnings_date ?? null,
      error: 'nightly fallback unavailable',
      fallback_reason: fallbackReason,
    };
  }
  return {
    ok: true,
    symbol: item.symbol,
    horizon_days: item.horizon_days,
    earnings_date: item.earnings_date ?? null,
    response,
  };
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid JSON body' }, { status: 400, headers: NO_STORE });
  }

  const parsed = BatchPredictRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid request', details: parsed.error.flatten() },
      { status: 400, headers: NO_STORE },
    );
  }

  if (!backendProxyConfigured()) {
    return NextResponse.json(
      {
        items: parsed.data.items.map((item) =>
          buildFallbackItem(item, 'backend_proxy_not_configured'),
        ),
        served_at: new Date().toISOString(),
      },
      { headers: NO_STORE },
    );
  }

  try {
    const upstream = await proxyJsonPost('/api/ml/batch-predict', parsed.data, {
      timeoutMs: 10_000,
    });
    return NextResponse.json(upstream, { headers: NO_STORE });
  } catch (err) {
    if (err instanceof BackendProxyError && err.status >= 400 && err.status < 500) {
      return NextResponse.json(
        { error: err.message },
        { status: err.status, headers: NO_STORE },
      );
    }
    return NextResponse.json(
      {
        items: parsed.data.items.map((item) =>
          buildFallbackItem(item, 'backend_unavailable'),
        ),
        served_at: new Date().toISOString(),
      },
      { headers: NO_STORE },
    );
  }
}
