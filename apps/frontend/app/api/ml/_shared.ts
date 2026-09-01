import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';

export const NO_STORE = {
  'Cache-Control': 'private, no-store, max-age=0, must-revalidate',
};

export const SYMBOL_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;
export const ML_DECISION_SCOPE = 'end_of_day_research' as const;
export const ML_MARKET_DATA_MODE = 'end_of_day' as const;

export const PredictRequestSchema = z.object({
  symbol: z.string().trim().toUpperCase().regex(SYMBOL_RE),
  horizon_days: z.union([
    z.literal(1),
    z.literal(2),
    z.literal(3),
    z.literal(7),
    z.literal(14),
    z.literal(21),
  ]),
  spot_override: z.number().positive().optional(),
  earnings_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  intended_use: z.literal(ML_DECISION_SCOPE).optional().default(ML_DECISION_SCOPE),
});

export type PredictRequestBody = z.infer<typeof PredictRequestSchema>;

export type BackendPredictResponse = {
  symbol: string;
  horizon_days: number;
  em_ml_pct: number;
  em_ml_abs: number;
  quantiles: Record<string, number>;
  spot_used: number;
  feature_snapshot_date: string;
  earnings_date: string | null;
  source: 'computed' | 'cached';
  inference_mode: 'snapshot_rescore' | 'spot_updated_snapshot';
  market_data_mode: typeof ML_MARKET_DATA_MODE;
  decision_scope: typeof ML_DECISION_SCOPE;
  live_trading_eligible: false;
  updated_inputs: Array<'spot'>;
  served_at: string;
  snapshot_age_days?: number | null;
  forecast_scored_at?: string | null;
  model_version?: string | null;
  model_trained_at?: string | null;
  model_loaded_at?: string | null;
  feature_schema_hash?: string | null;
};

export type SymbolJson = {
  spot_price?: number | null;
  as_of_date?: string;
  expected_move?: {
    earnings_date?: string | null;
    straddle_abs?: number | null;
    straddle_pct?: number | null;
    iv_pct?: number | null;
    em_ml_pct?: number | null;
    em_ml_abs?: number | null;
    ml_snapshot_date?: string | null;
    p10?: number | null;
    p25?: number | null;
    p50?: number | null;
    p75?: number | null;
    p90?: number | null;
  };
};

export type NightlyFallbackKind = 'static_ml' | 'straddle';

export type NightlyFallbackPayload = {
  symbol: string;
  horizon_days: number;
  em_ml_pct: number;
  em_ml_abs: number;
  quantiles: Record<string, number>;
  spot_used: number;
  feature_snapshot_date: string | null;
  earnings_date: string | null;
  source: 'nightly_fallback';
  inference_mode: 'nightly_snapshot';
  market_data_mode: typeof ML_MARKET_DATA_MODE;
  decision_scope: typeof ML_DECISION_SCOPE;
  live_trading_eligible: false;
  updated_inputs: Array<'spot_for_dollar_scaling'>;
  fallback_kind: NightlyFallbackKind;
  fallback_reason: string;
  served_at: string;
};

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function publicDir(): string {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public'),
    join(process.cwd(), 'public'),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return candidates[0];
}

export function loadSymbolJson(symbol: string): SymbolJson | null {
  const path = join(publicDir(), 'symbols', `${symbol}.json`);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as SymbolJson;
  } catch {
    return null;
  }
}

function quantilesFromExpectedMove(
  expectedMove: SymbolJson['expected_move'],
): Record<string, number> {
  if (!expectedMove) return {};
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries({
    '10': expectedMove.p10,
    '25': expectedMove.p25,
    '50': expectedMove.p50,
    '75': expectedMove.p75,
    '90': expectedMove.p90,
  })) {
    if (finite(value)) out[key] = value;
  }
  return out;
}

export function buildNightlyFallbackPayload(
  req: PredictRequestBody,
  nightly: SymbolJson | null,
  fallbackReason: string,
): NightlyFallbackPayload | null {
  const sym = req.symbol;
  const expectedMove = nightly?.expected_move;
  const spot = req.spot_override ?? nightly?.spot_price ?? 0;
  const staticMlAbs = finite(expectedMove?.em_ml_abs) ? expectedMove.em_ml_abs : null;
  const straddleAbs = finite(expectedMove?.straddle_abs) ? expectedMove.straddle_abs : null;
  const staticMlPct = finite(expectedMove?.em_ml_pct)
    ? expectedMove.em_ml_pct
    : staticMlAbs !== null && spot > 0
      ? staticMlAbs / spot
      : null;
  const straddlePct = finite(expectedMove?.straddle_pct)
    ? expectedMove.straddle_pct
    : straddleAbs !== null && spot > 0
      ? straddleAbs / spot
      : null;
  const pct = staticMlPct ?? straddlePct;
  if (pct === null) return null;
  const fallbackKind: NightlyFallbackKind = staticMlPct !== null ? 'static_ml' : 'straddle';
  const emAbs = spot > 0 ? pct * spot : staticMlAbs ?? straddleAbs ?? 0;
  const quantiles = fallbackKind === 'static_ml'
    ? quantilesFromExpectedMove(expectedMove)
    : {};

  return {
    symbol: sym,
    horizon_days: req.horizon_days,
    em_ml_pct: pct,
    em_ml_abs: emAbs,
    quantiles,
    spot_used: spot,
    feature_snapshot_date:
      fallbackKind === 'static_ml'
        ? expectedMove?.ml_snapshot_date ?? nightly?.as_of_date ?? null
        : nightly?.as_of_date ?? null,
    earnings_date: req.earnings_date ?? expectedMove?.earnings_date ?? null,
    source: 'nightly_fallback',
    inference_mode: 'nightly_snapshot',
    market_data_mode: ML_MARKET_DATA_MODE,
    decision_scope: ML_DECISION_SCOPE,
    live_trading_eligible: false,
    updated_inputs: req.spot_override == null ? [] : ['spot_for_dollar_scaling'],
    fallback_kind: fallbackKind,
    fallback_reason: fallbackReason,
    served_at: new Date().toISOString(),
  };
}

export function nightlyFallbackResponse(
  req: PredictRequestBody,
  fallbackReason: string,
): Response {
  const payload = buildNightlyFallbackPayload(
    req,
    loadSymbolJson(req.symbol),
    fallbackReason,
  );
  if (!payload) {
    return Response.json(
      {
        error: 'nightly fallback unavailable',
        symbol: req.symbol,
        horizon_days: req.horizon_days,
        earnings_date: req.earnings_date ?? null,
        fallback_reason: fallbackReason,
      },
      { status: 503, headers: NO_STORE },
    );
  }
  return Response.json(
    payload,
    { headers: NO_STORE },
  );
}
