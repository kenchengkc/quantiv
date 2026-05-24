/**
 * POST /api/ml/predict — Vercel-side proxy for the Railway FastAPI route.
 *
 * Flow (in order):
 *   1. Validate the request body with Zod.
 *   2. HMAC-proxy to Railway `/api/ml/predict` with a 5 s timeout.
 *   3. On Railway 4xx (model says no): return it as-is so the client can
 *      show a precise error (e.g. "no recent snapshot for this symbol").
 *   4. On Railway timeout / 5xx / network error: fall back to the nightly
 *      number from /public/symbols/{SYM}.json (straddle_pct as the move,
 *      no ML quantiles), labelled `source: 'nightly_fallback'`. UI shows
 *      a subdued "live unavailable" badge instead of an error.
 *
 * Why a proxy at all (instead of letting the browser call Railway):
 *   - Hides BACKEND_SHARED_SECRET from the client.
 *   - Keeps the public origin single-domain — Vercel CDN edge logic
 *     stays consistent and CORS stays simple.
 *   - Lets us substitute the nightly fallback transparently.
 */

import { NextRequest, NextResponse } from 'next/server';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { BackendProxyError, backendProxyConfigured, proxyJsonPost } from '@/lib/backendProxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const VALID_HORIZONS = [1, 2, 3, 7, 14, 21] as const;
const SYMBOL_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

const RequestSchema = z.object({
  symbol: z.string().trim().toUpperCase().regex(SYMBOL_RE),
  horizon_days: z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(7), z.literal(14), z.literal(21)]),
  spot_override: z.number().positive().optional(),
  earnings_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
});

type RequestBody = z.infer<typeof RequestSchema>;

type BackendResponse = {
  symbol: string;
  horizon_days: number;
  em_ml_pct: number;
  em_ml_abs: number;
  quantiles: Record<string, number>;
  spot_used: number;
  feature_snapshot_date: string;
  earnings_date: string | null;
  source: 'live' | 'cached';
  served_at: string;
};

type SymbolJson = {
  spot_price?: number | null;
  as_of_date?: string;
  expected_move?: {
    straddle_pct?: number | null;
    iv_pct?: number | null;
  };
};

// Same private/no-store header as the watchlist routes — predictions are
// user-input-dependent (spot_override) and a CDN cache would silently
// share one user's hit with another.
const NO_STORE = {
  'Cache-Control': 'private, no-store, max-age=0, must-revalidate',
};

function publicDir(): string {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public'),
    join(process.cwd(), 'public'),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return candidates[0];
}

function loadSymbolJson(symbol: string): SymbolJson | null {
  const path = join(publicDir(), 'symbols', `${symbol}.json`);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as SymbolJson;
  } catch {
    return null;
  }
}

function nightlyFallback(req: RequestBody): NextResponse {
  const sym = req.symbol;
  const nightly = loadSymbolJson(sym);
  const straddlePct = nightly?.expected_move?.straddle_pct ?? null;
  const spot = req.spot_override ?? nightly?.spot_price ?? 0;
  const emAbs = straddlePct !== null && spot > 0 ? straddlePct * spot : 0;
  return NextResponse.json(
    {
      symbol: sym,
      horizon_days: req.horizon_days,
      em_ml_pct: straddlePct ?? 0,
      em_ml_abs: emAbs,
      quantiles: {} as Record<string, number>,
      spot_used: spot,
      feature_snapshot_date: nightly?.as_of_date ?? null,
      earnings_date: req.earnings_date ?? null,
      source: 'nightly_fallback' as const,
      served_at: new Date().toISOString(),
    },
    { headers: NO_STORE },
  );
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid JSON body' }, { status: 400, headers: NO_STORE });
  }
  const parsed = RequestSchema.safeParse(body);
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
    return nightlyFallback(requestBody);
  }

  try {
    const upstream = await proxyJsonPost<BackendResponse>('/api/ml/predict', requestBody, {
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
    return nightlyFallback(requestBody);
  }
}
