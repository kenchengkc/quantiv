import { describe, expect, it } from 'vitest';
import {
  displayForecastClass,
  displayForecastLabel,
  finiteDisplayForecast,
  resolveDisplayForecastCompat,
} from './displayForecast';

describe('display forecast helpers', () => {
  it('uses compact provenance labels', () => {
    expect(displayForecastLabel('ml')).toBe('ML forecast');
    expect(displayForecastLabel('options_math')).toBe('IV forecast');
    expect(displayForecastLabel('options_indicative')).toBe('IV forecast');
    expect(displayForecastLabel('historical')).toBe('Historical median');
    expect(displayForecastLabel('historical_prior')).toBe('Historical prior');
  });

  it('maps methods onto semantic style classes without badges', () => {
    expect(displayForecastClass('ml')).toContain('qv-forecast--ml');
    expect(displayForecastClass('options_math')).toContain('qv-forecast--options');
    expect(displayForecastClass('options_indicative')).toContain('qv-forecast--indicative');
    expect(displayForecastClass('historical')).toContain('qv-forecast--historical');
    expect(displayForecastClass('historical_prior')).toContain('qv-forecast--prior');
  });

  it('accepts only finite positive display forecasts', () => {
    expect(finiteDisplayForecast(0.04)).toBe(0.04);
    expect(finiteDisplayForecast(0)).toBeNull();
    expect(finiteDisplayForecast(Number.NaN)).toBeNull();
    expect(finiteDisplayForecast(null)).toBeNull();
  });

  it('bridges legacy fields using ML before IV/options and historical data', () => {
    expect(resolveDisplayForecastCompat({ em_ml_pct: 0.12, em_straddle_pct: 0.14 })).toEqual({
      pct: 0.12,
      method: 'ml',
    });
    expect(resolveDisplayForecastCompat({ em_ml_pct: null, em_straddle_pct: 0.11 })).toEqual({
      pct: 0.11,
      method: 'options_math',
    });
    expect(resolveDisplayForecastCompat({ em_ml_pct: null, straddle_pct: 0.09, iv_pct: 0.10 })).toEqual({
      pct: 0.10,
      method: 'options_math',
    });
    expect(
      resolveDisplayForecastCompat({
        display_forecast_pct: 0.07,
        display_forecast_method: 'historical',
        em_ml_pct: 0.12,
        em_straddle_pct: 0.14,
        em_iv_pct: 0.16,
      }),
    ).toEqual({ pct: 0.12, method: 'ml' });
    expect(
      resolveDisplayForecastCompat({
        display_forecast_pct: 0.11,
        display_forecast_method: 'options_indicative',
        em_ml_pct: 0.12,
      }),
    ).toEqual({ pct: 0.12, method: 'ml' });
  });

  it('uses a historical compatibility estimate when legacy public data has no ML or options move', () => {
    expect(
      resolveDisplayForecastCompat(
        {
          em_ml_pct: null,
          em_straddle_pct: null,
          em_iv_pct: null,
        },
        0.081,
      ),
    ).toEqual({ pct: 0.081, method: 'historical' });
  });

  it('uses legacy calendar history instead of a dash when canonical display fields are absent', () => {
    expect(
      resolveDisplayForecastCompat({
        em_ml_pct: null,
        em_straddle_pct: null,
        em_iv_pct: null,
        hist_move_med_4q: 0.091,
        hist_move_avg_4q: 0.102694,
      }),
    ).toEqual({ pct: 0.091, method: 'historical' });

    expect(
      resolveDisplayForecastCompat({
        em_ml_pct: null,
        em_straddle_pct: null,
        em_iv_pct: null,
        hist_move_med_4q: null,
        hist_move_avg_4q: 0.102694,
      }),
    ).toEqual({ pct: 0.102694, method: 'historical' });
  });
});
