export type DisplayForecastMethod =
  | 'ml'
  | 'options_math'
  | 'options_indicative'
  | 'historical'
  | 'historical_prior';

export type MLForecastStatus =
  | 'available'
  | 'unavailable_inputs'
  | 'unavailable_model'
  | 'unavailable_event';

export type OptionsForecastStatus = 'decision_eligible' | 'indicative' | 'unavailable';

export type ForecastFallbackReason =
  | 'quote_quality'
  | 'no_same_strike_pair'
  | 'no_event_expiry'
  | 'insufficient_ticker_history'
  | null;

export type DisplayForecastFields = {
  display_forecast_pct?: number | null;
  display_forecast_method?: DisplayForecastMethod | null;
  display_forecast_as_of?: string | null;
  ml_status?: MLForecastStatus | null;
  options_status?: OptionsForecastStatus | null;
  fallback_reason?: ForecastFallbackReason;
  historical_event_count?: number | null;
};

type LegacyDisplayForecastFields = DisplayForecastFields & {
  em_ml_pct?: number | null;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  straddle_pct?: number | null;
  iv_pct?: number | null;
};

export function displayForecastLabel(method: DisplayForecastMethod | null | undefined): string {
  switch (method) {
    case 'ml':
      return 'ML forecast';
    case 'options_math':
    case 'options_indicative':
      return 'Market implied';
    case 'historical':
      return 'Historical median';
    case 'historical_prior':
      return 'Historical prior';
    default:
      return 'Expected move';
  }
}

export function displayForecastClass(method: DisplayForecastMethod | null | undefined): string {
  switch (method) {
    case 'ml':
      return 'qv-forecast qv-forecast--ml';
    case 'options_math':
      return 'qv-forecast qv-forecast--options';
    case 'options_indicative':
      return 'qv-forecast qv-forecast--indicative';
    case 'historical':
      return 'qv-forecast qv-forecast--historical';
    case 'historical_prior':
      return 'qv-forecast qv-forecast--prior';
    default:
      return 'qv-forecast';
  }
}

export function isOptionsDisplayForecast(
  method: DisplayForecastMethod | null | undefined,
): boolean {
  return method === 'options_math' || method === 'options_indicative';
}

export function isHistoricalDisplayForecast(
  method: DisplayForecastMethod | null | undefined,
): boolean {
  return method === 'historical' || method === 'historical_prior';
}

export function finiteDisplayForecast(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * Transitional read compatibility for source-controlled JSON generated before
 * the display-forecast contract landed. Canonical fields always win; only when
 * they are absent do we reconstruct the old presentation hierarchy (ML first,
 * then strict options/IV). This is display-only and never changes analytical
 * eligibility or ML-only filtering.
 */
export function resolveDisplayForecastCompat(
  fields: LegacyDisplayForecastFields | null | undefined,
): { pct: number | null; method: DisplayForecastMethod | null } {
  if (!fields) return { pct: null, method: null };

  const canonical = finiteDisplayForecast(fields.display_forecast_pct);
  if (canonical != null) {
    return {
      pct: canonical,
      method: fields.display_forecast_method ?? null,
    };
  }

  const ml = finiteDisplayForecast(fields.em_ml_pct);
  if (ml != null) return { pct: ml, method: 'ml' };

  const implied =
    finiteDisplayForecast(fields.em_straddle_pct) ??
    finiteDisplayForecast(fields.straddle_pct) ??
    finiteDisplayForecast(fields.em_iv_pct) ??
    finiteDisplayForecast(fields.iv_pct);
  if (implied != null) return { pct: implied, method: 'options_math' };

  return { pct: null, method: fields.display_forecast_method ?? null };
}
