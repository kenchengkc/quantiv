/**
 * POST /api/ml/predict — Vercel-side proxy for the Railway FastAPI route.
 *
 * Flow (in order):
 *   1. Validate the request body with Zod.
 *   2. HMAC-proxy to Railway `/api/ml/predict` with a 5 s timeout.
 *   3. On Railway 4xx (model says no): return it as-is so the client can
 *      show a precise error (e.g. "no recent snapshot for this symbol").
 *   4. On Railway timeout / 5xx / network error: fall back to the nightly
 *      number from /public/symbols/{SYM}.json. Prefer static ML fields
 *      (`em_ml_pct`, quantiles) and only fall back to straddle when ML is
 *      absent.
 *
 * Why a proxy at all (instead of letting the browser call Railway):
 *   - Hides BACKEND_SHARED_SECRET from the client.
 *   - Keeps the public origin single-domain — Vercel CDN edge logic
 *     stays consistent and CORS stays simple.
 *   - Lets us substitute the nightly fallback transparently.
 */

import { NextRequest, NextResponse } from 'next/server';
import { BackendProxyError, backendProxyConfigured, proxyJsonPost } from '@/lib/backendProxy';
import {
  BackendPredictResponse,
  NO_STORE,
  PredictRequestSchema,
  nightlyFallbackResponse,
} from '../_shared';
import { enforceRateLimit, PUBLIC_RATE_LIMITS } from '@/lib/rateLimit';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function POST(req: NextRequest) {
  const rateLimited = await enforceRateLimit(req, PUBLIC_RATE_LIMITS.mlPredict);
  if (rateLimited) return rateLimited;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid JSON body' }, { status: 400, headers: NO_STORE });
  }
  const parsed = PredictRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'invalid request', details: parsed.error.flatten() },
      { status: 400, headers: NO_STORE },
    );
  }
  const requestBody = parsed.data;

  // No backend wired up → fall back to nightly immediately so the route
  // works in local dev without Railway env set.
  if (!backendProxyConfigured()) {
    return nightlyFallbackResponse(requestBody, 'backend_proxy_not_configured');
  }

  try {
    const upstream = await proxyJsonPost<BackendPredictResponse>('/api/ml/predict', requestBody, {
      timeoutMs: 5_000,
    });
    return NextResponse.json(upstream, { headers: NO_STORE });
  } catch (err) {
    if (err instanceof BackendProxyError) {
      // 4xx from Railway is a real user-visible error ("no fresh snapshot
      // for this symbol", "horizon must be one of ...") — surface it as-is
      // rather than masking with the fallback.
      if (err.status >= 400 && err.status < 500) {
        return NextResponse.json({ error: err.message }, { status: err.status, headers: NO_STORE });
      }
      // 5xx / timeout / network — degrade to the nightly fallback so the
      // UI shows a number with a clear "live unavailable" badge instead
      // of an error.
    }
    return nightlyFallbackResponse(requestBody, 'backend_unavailable');
  }
}
