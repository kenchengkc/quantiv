import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { BackendProxyError, backendProxyConfigured, proxyJsonPost } from '@/lib/backendProxy';
import { enforceRateLimit, PUBLIC_RATE_LIMITS } from '@/lib/rateLimit';
import {
  NO_STORE,
  PredictRequestSchema,
  type PredictRequestBody,
  type SymbolJson,
  buildNightlyFallbackPayload,
  loadPredictionPublicationPolicy,
  loadSymbolJson,
} from '../_shared';
import { guardPublishedPrediction, heldPrediction, matchesPublishedPrediction } from '../_publicationGuard';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const BatchPredictRequestSchema = z.object({
  items: z.array(PredictRequestSchema).min(1).max(100),
  allow_partial: z.boolean().optional().default(true),
});

function buildFallbackItem(
  item: PredictRequestBody,
  fallbackReason: string,
  nightly: SymbolJson | null = loadSymbolJson(item.symbol),
) {
  const response = buildNightlyFallbackPayload(
    item,
    nightly,
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
  const rateLimited = await enforceRateLimit(req, PUBLIC_RATE_LIMITS.mlBatchPredict);
  if (rateLimited) return rateLimited;

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

  const policyActive = loadPredictionPublicationPolicy();
  const publication = parsed.data.items.map((item) => {
    const nightly = loadSymbolJson(item.symbol);
    return { ...guardPublishedPrediction(item, nightly, policyActive), nightly };
  });
  const allowed = publication.filter((item) => item.holdReason == null);
  const mergeItems = (responses: unknown[]) => {
    let nextResponse = 0;
    return publication.map((item) => item.holdReason
      ? heldPrediction(item.request, item.holdReason)
      : responses[nextResponse++]);
  };

  if (allowed.length === 0 || !backendProxyConfigured()) {
    return NextResponse.json(
      {
        items: mergeItems(allowed.map((item) =>
          buildFallbackItem(item.request, 'backend_proxy_not_configured', item.nightly))),
        served_at: new Date().toISOString(),
      },
      { headers: NO_STORE },
    );
  }

  try {
    const upstream = await proxyJsonPost<{ items: unknown[]; served_at: string }>(
      '/api/ml/batch-predict', {
        ...parsed.data, items: allowed.map((item) => item.request),
      }, {
        timeoutMs: 10_000,
      },
    );
    if (!Array.isArray(upstream?.items) || upstream.items.length !== allowed.length) {
      return NextResponse.json({
        items: mergeItems(allowed.map((item) =>
          buildFallbackItem(item.request, 'backend_batch_response_invalid', item.nightly))),
        served_at: new Date().toISOString(),
      }, { headers: NO_STORE });
    }
    const responses = allowed.map((item, index) => {
      const result = upstream.items[index];
      if (!item.publicationBound) return result;
      const entry = result && typeof result === 'object' ? result as Record<string, unknown> : null;
      if (entry?.ok !== true || entry.symbol !== item.request.symbol
        || entry.horizon_days !== item.request.horizon_days
        || entry.earnings_date !== item.request.earnings_date
        || !matchesPublishedPrediction(item.request, item.nightly, entry.response)) {
        return buildFallbackItem(item.request, 'backend_prediction_not_current', item.nightly);
      }
      return result;
    });
    return NextResponse.json({ ...upstream, items: mergeItems(responses) }, { headers: NO_STORE });
  } catch (err) {
    if (err instanceof BackendProxyError && err.status >= 400 && err.status < 500) {
      if (allowed.some((item) => item.publicationBound)) {
        return NextResponse.json({
          items: mergeItems(allowed.map((item) => item.publicationBound
            ? buildFallbackItem(item.request, 'backend_prediction_unavailable', item.nightly)
            : { ok: false, symbol: item.request.symbol, horizon_days: item.request.horizon_days,
              earnings_date: item.request.earnings_date ?? null,
              error_status: err.status, error: err.message })),
          served_at: new Date().toISOString(),
        }, { headers: NO_STORE });
      }
      return NextResponse.json(
        { error: err.message },
        { status: err.status, headers: NO_STORE },
      );
    }
    return NextResponse.json(
      {
        items: mergeItems(allowed.map((item) =>
          buildFallbackItem(item.request, 'backend_unavailable', item.nightly))),
        served_at: new Date().toISOString(),
      },
      { headers: NO_STORE },
    );
  }
}
