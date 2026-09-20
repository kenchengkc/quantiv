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
  hist_move_med_4q?: number | null;
  hist_move_avg_4q?: number | null;
};

export function displayForecastLabel(method: DisplayForecastMethod | null | undefined): string {
  switch (method) {
    case 'ml':
      return 'ML forecast';
    case 'options_math':
    case 'options_indicative':
      return 'IV forecast';
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
 * Transitional read compatibility for public JSON generated before the
 * display-forecast contract landed.
 *
 * Product headline priority is ML -> IV/options -> historical. This helper
 * intentionally ignores a stale canonical historical/options field when a
 * stronger raw ML signal is present so calendar, screener, watchlist and symbol
 * pages cannot drift during a publication transition.
 */
export function resolveDisplayForecastCompat(
  fields: LegacyDisplayForecastFields | null | undefined,
  historicalFallbackPct: number | null | undefined = null,
): { pct: number | null; method: DisplayForecastMethod | null } {
  const explicitHistorical = finiteDisplayForecast(historicalFallbackPct);
  if (!fields) {
    return explicitHistorical != null
      ? { pct: explicitHistorical, method: 'historical' }
      : { pct: null, method: null };
  }

  const ml = finiteDisplayForecast(fields.em_ml_pct);
  if (ml != null) return { pct: ml, method: 'ml' };

  const canonical = finiteDisplayForecast(fields.display_forecast_pct);
  if (canonical != null && fields.display_forecast_method === 'ml') {
    return { pct: canonical, method: 'ml' };
  }

  const iv =
    finiteDisplayForecast(fields.em_iv_pct) ??
    finiteDisplayForecast(fields.iv_pct);
  if (iv != null) return { pct: iv, method: 'options_math' };

  const straddle =
    finiteDisplayForecast(fields.em_straddle_pct) ??
    finiteDisplayForecast(fields.straddle_pct);
  if (straddle != null) return { pct: straddle, method: 'options_math' };

  if (
    canonical != null &&
    (fields.display_forecast_method === 'options_math' ||
      fields.display_forecast_method === 'options_indicative')
  ) {
    return { pct: canonical, method: fields.display_forecast_method };
  }

  const canonicalHistorical =
    canonical != null &&
    (fields.display_forecast_method === 'historical' ||
      fields.display_forecast_method === 'historical_prior')
      ? canonical
      : null;
  const historical =
    explicitHistorical ??
    canonicalHistorical ??
    finiteDisplayForecast(fields.hist_move_med_4q) ??
    finiteDisplayForecast(fields.hist_move_avg_4q);
  if (historical != null) {
    return {
      pct: historical,
      method:
        explicitHistorical != null
          ? 'historical'
          : canonicalHistorical != null
            ? fields.display_forecast_method ?? 'historical'
            : 'historical',
    };
  }

  if (canonical != null) {
    return {
      pct: canonical,
      method: fields.display_forecast_method ?? null,
    };
  }

  return { pct: null, method: fields.display_forecast_method ?? null };
}
