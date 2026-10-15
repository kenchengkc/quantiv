export type DisplayForecastMethod =
  | 'ml'
  | 'ml_unavailable'
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
  | 'options_held'
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
  ml_publication_status?: 'held' | 'eligible' | null;
  ml_publication_reason?: string | null;
  options_status?: OptionsForecastStatus | null;
  fallback_reason?: ForecastFallbackReason;
  historical_event_count?: number | null;
  options_publication_status?: 'held' | null;
  options_publication_source_date?: string | null;
};

type LegacyDisplayForecastFields = DisplayForecastFields & {
  earnings_date?: string | null;
  date?: string | null;
  eps_actual?: number | null;
  revenue_actual?: number | null;
  actual?: number | null;
  realized_move_pct?: number | null;
  em_ml_pct?: number | null;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  straddle_pct?: number | null;
  iv_pct?: number | null;
  hist_move_med_4q?: number | null;
  hist_move_avg_4q?: number | null;
};

const MARKET_DATE_FORMAT = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
});

export function displayForecastLabel(method: DisplayForecastMethod | null | undefined): string {
  switch (method) {
    case 'ml':
      return 'ML forecast';
    case 'ml_unavailable':
      return 'ML unavailable';
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

/** Resolve an upcoming ML headline while retaining reported-history compatibility. */
export function resolveDisplayForecastCompat(
  fields: LegacyDisplayForecastFields | null | undefined,
  historicalFallbackPct: number | null | undefined = null,
  earningsDate?: string | null,
): { pct: number | null; method: DisplayForecastMethod | null } {
  const eventDate = (earningsDate ?? fields?.earnings_date ?? fields?.date)?.slice(0, 10);
  const today = MARKET_DATE_FORMAT.format(new Date());
  const hasActual = [fields?.eps_actual, fields?.revenue_actual, fields?.actual, fields?.realized_move_pct]
    .some((value) => value != null && Number.isFinite(value));
  if (fields?.display_forecast_method === 'ml_unavailable') return { pct: null, method: 'ml_unavailable' };
  if (eventDate && eventDate >= today && !hasActual) {
    const ml = fields?.ml_publication_status === 'eligible' &&
      (fields.ml_status == null || fields.ml_status === 'available')
      ? finiteDisplayForecast(fields.em_ml_pct) ??
        (fields.display_forecast_method === 'ml' ? finiteDisplayForecast(fields.display_forecast_pct) : null)
      : null;
    return ml != null ? { pct: ml, method: 'ml' } : { pct: null, method: 'ml_unavailable' };
  }
  const explicitHistorical = finiteDisplayForecast(historicalFallbackPct);
  if (!fields) {
    return explicitHistorical != null
      ? { pct: explicitHistorical, method: 'historical' }
      : { pct: null, method: null };
  }

  const mlHeld = fields.ml_publication_status === 'held';
  const ml = mlHeld ? null : finiteDisplayForecast(fields.em_ml_pct);
  if (ml != null) return { pct: ml, method: 'ml' };

  const canonical = mlHeld && fields.display_forecast_method === 'ml'
    ? null : finiteDisplayForecast(fields.display_forecast_pct);
  if (canonical != null && fields.display_forecast_method === 'ml') {
    return { pct: canonical, method: 'ml' };
  }

  // A held projection already selected history at its audited cutoff. A
  // later compact median or retained option input cannot replace that value.
  if (
    canonical != null &&
    fields.options_publication_status === 'held' &&
    fields.options_status === 'unavailable' &&
    fields.options_publication_source_date &&
    fields.options_publication_source_date === fields.display_forecast_as_of &&
    (fields.display_forecast_method === 'historical' ||
      fields.display_forecast_method === 'historical_prior')
  ) {
    return { pct: canonical, method: fields.display_forecast_method };
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

  return { pct: null, method: mlHeld && fields.display_forecast_method === 'ml'
    ? null : fields.display_forecast_method ?? null };
}
