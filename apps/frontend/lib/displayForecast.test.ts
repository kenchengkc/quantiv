import { describe, expect, it } from 'vitest';
import {
  displayForecastClass,
  displayForecastLabel,
  finiteDisplayForecast,
  resolveDisplayForecastCompat,
} from './displayForecast';

describe('display forecast helpers', () => {
  it('ignores held raw and canonical ML values while retaining independent fallback', () => {
    expect(resolveDisplayForecastCompat({
      ml_publication_status: 'held', em_ml_pct: .4, display_forecast_pct: .4,
      display_forecast_method: 'ml', em_iv_pct: .08,
    })).toEqual({ pct: .08, method: 'options_math' });
    expect(resolveDisplayForecastCompat({
      ml_publication_status: 'held', display_forecast_pct: .4,
      display_forecast_method: 'ml',
    }, .06)).toEqual({ pct: .06, method: 'historical' });
    expect(resolveDisplayForecastCompat({
      ml_publication_status: 'held', display_forecast_pct: .4, display_forecast_method: 'ml',
    }).pct).toBeNull();
  });
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

  it.each(['historical', 'historical_prior'] as const)(
    'preserves a held %s forecast cutoff ahead of legacy IV and later compact history',
    (method) => {
      const fields = {
        display_forecast_pct: 0.09,
        display_forecast_method: method,
        display_forecast_as_of: '2026-09-16',
        iv_pct: 0.20,
        options_status: 'unavailable',
        options_publication_status: 'held',
        options_publication_source_date: '2026-09-16',
      } as const;

      expect(resolveDisplayForecastCompat(fields, 0.06)).toEqual({ pct: 0.09, method });
      expect(resolveDisplayForecastCompat({ ...fields, em_ml_pct: 0.12 }, 0.06))
        .toEqual({ pct: 0.12, method: 'ml' });
      expect(resolveDisplayForecastCompat({
        ...fields, options_publication_source_date: '2026-09-15',
      }, 0.06)).toEqual({ pct: 0.20, method: 'options_math' });
    },
  );
});


describe('upcoming ML headline contract', () => {
  it.each(['historical', 'historical_prior', 'options_math', 'options_indicative'] as const)(
    'does not resurrect %s from upcoming legacy payloads', (method) => {
      expect(resolveDisplayForecastCompat({
        earnings_date: '2099-10-21', display_forecast_method: method,
        display_forecast_pct: 0.08, em_iv_pct: 0.12, hist_move_med_4q: 0.09,
      }, 0.10)).toEqual({ pct: null, method: 'ml_unavailable' });
    },
  );
  it('requires eligible ML publication on upcoming rows', () => {
    expect(resolveDisplayForecastCompat({
      earnings_date: '2099-10-21', em_ml_pct: 0.07,
      ml_publication_status: 'eligible',
    })).toEqual({ pct: 0.07, method: 'ml' });
    expect(resolveDisplayForecastCompat({
      earnings_date: '2099-10-21', em_ml_pct: 0.07,
      ml_publication_status: 'held', em_iv_pct: 0.12,
    })).toEqual({ pct: null, method: 'ml_unavailable' });
    expect(resolveDisplayForecastCompat({
      earnings_date: '2099-10-21', em_ml_pct: 0.07,
    })).toEqual({ pct: null, method: 'ml_unavailable' });
  });
  it('does not use symbol history when an upcoming hero has no expected-move row', () => {
    expect(resolveDisplayForecastCompat({}, 0.09, '2099-10-21'))
      .toEqual({ pct: null, method: 'ml_unavailable' });
  });
  it('preserves a reported forecast even when actual EPS is zero', () => {
    expect(resolveDisplayForecastCompat({
      earnings_date: '2099-10-21', eps_actual: 0,
      display_forecast_pct: 0.08, display_forecast_method: 'historical',
    })).toEqual({ pct: 0.08, method: 'historical' });
  });
  it('labels the unavailable state explicitly', () => {
    expect(displayForecastLabel('ml_unavailable')).toBe('ML unavailable');
  });
});


it('preserves explicit unavailable publication even beside stale eligible raw ML', () => {
  expect(resolveDisplayForecastCompat({
    display_forecast_method: 'ml_unavailable', display_forecast_pct: null,
    em_ml_pct: 0.07, ml_publication_status: 'eligible',
  })).toEqual({ pct: null, method: 'ml_unavailable' });
});
