import { describe, expect, it } from 'vitest';
import { PredictRequestSchema, buildNightlyFallbackPayload } from './_shared';

describe('ML nightly fallback payload', () => {
  it('prefers static nightly ML fields over straddle fields', () => {
    const payload = buildNightlyFallbackPayload(
      {
        symbol: 'CRM',
        horizon_days: 7,
        spot_override: 181.19,
        earnings_date: '2026-05-27',
      },
      {
        spot_price: 182.5,
        as_of_date: '2026-05-22',
        expected_move: {
          earnings_date: '2026-05-27',
          straddle_pct: 0.103288,
          em_ml_pct: 0.069509,
          em_ml_abs: 12.518525,
          ml_snapshot_date: '2026-05-20',
          p10: 0.010664,
          p25: 0.029028,
          p50: 0.051936,
          p75: 0.106932,
          p90: 0.171874,
        },
      },
      'backend_unavailable',
    );

    expect(payload).not.toBeNull();
    if (!payload) return;
    expect(payload.source).toBe('nightly_fallback');
    expect(payload.fallback_kind).toBe('static_ml');
    expect(payload.fallback_reason).toBe('backend_unavailable');
    expect(payload.em_ml_pct).toBe(0.069509);
    expect(payload.em_ml_abs).toBeCloseTo(0.069509 * 181.19);
    expect(payload.feature_snapshot_date).toBe('2026-05-20');
    expect(payload.inference_mode).toBe('nightly_snapshot');
    expect(payload.market_data_mode).toBe('end_of_day');
    expect(payload.decision_scope).toBe('end_of_day_research');
    expect(payload.live_trading_eligible).toBe(false);
    expect(payload.updated_inputs).toEqual(['spot_for_dollar_scaling']);
    expect(payload.quantiles).toEqual({
      '10': 0.010664,
      '25': 0.029028,
      '50': 0.051936,
      '75': 0.106932,
      '90': 0.171874,
    });
  });

  it('falls back to straddle when static ML fields are absent', () => {
    const payload = buildNightlyFallbackPayload(
      { symbol: 'ABC', horizon_days: 7, spot_override: 50 },
      {
        as_of_date: '2026-05-22',
        expected_move: {
          earnings_date: '2026-05-29',
          straddle_pct: 0.08,
        },
      },
      'backend_proxy_not_configured',
    );

    expect(payload).not.toBeNull();
    if (!payload) return;
    expect(payload.fallback_kind).toBe('straddle');
    expect(payload.em_ml_pct).toBe(0.08);
    expect(payload.em_ml_abs).toBe(4);
    expect(payload.quantiles).toEqual({});
  });

  it('returns unavailable instead of inventing a zero move', () => {
    const payload = buildNightlyFallbackPayload(
      { symbol: 'EMPTY', horizon_days: 7, spot_override: 50 },
      {
        spot_price: 50,
        as_of_date: '2026-05-22',
        expected_move: {
          earnings_date: '2026-05-29',
        },
      },
      'backend_unavailable',
    );

    expect(payload).toBeNull();
  });

  it('fails closed when a caller requests live-trading use', () => {
    expect(
      PredictRequestSchema.safeParse({
        symbol: 'CRM',
        horizon_days: 7,
        spot_override: 181.19,
        intended_use: 'live_trading',
      }).success,
    ).toBe(false);
    expect(
      PredictRequestSchema.parse({
        symbol: 'CRM',
        horizon_days: 7,
      }).intended_use,
    ).toBe('end_of_day_research');
  });
});
