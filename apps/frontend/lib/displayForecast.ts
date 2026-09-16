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
