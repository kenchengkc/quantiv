export interface Straddle {
  expiration: string;
  dte: number;
  atm_strike: number;
  atm_iv: number | null;
  atm_call_iv: number | null;
  atm_put_iv: number | null;
  straddle_mid: number | null;
  em_straddle: number | null;
  em_straddle_pct: number | null;
  em_iv: number | null;
  em_iv_pct: number | null;
  call_delta: number | null;
  call_gamma: number | null;
  call_vega: number | null;
  call_theta: number | null;
}

export interface ExpectedMove {
  earnings_date?: string;
  expiration: string;
  dte: number;
  lead_time_days?: number;
  atm_strike: number;
  atm_iv: number | null;
  straddle_abs: number | null;
  straddle_pct: number | null;
  iv_pct: number | null;
  skew_atm?: number | null;
  term_slope?: number | null;
  total_vega?: number | null;
  timing?: string;
  em_method?: 'options_math' | 'ml_lightgbm' | 'ensemble';
  em_ml_pct?: number | null;
  em_ml_abs?: number | null;
  correction_factor?: number | null;
  model_horizon?: number | null;
  ml_snapshot_date?: string | null;
  p10?: number | null;
  p25?: number | null;
  p50?: number | null;
  p75?: number | null;
  p90?: number | null;
}

export interface VolRegime {
  iv_current: number | null;
  iv_rank: number | null;
  iv_year_high: number | null;
  iv_year_low: number | null;
  hv_current: number | null;
  hv_rank: number | null;
  iv_mom_week: number | null;
  iv_mom_month: number | null;
}

export interface ProviderEnrichment {
  short_interest?: {
    days_to_cover?: number | null;
    shares?: number | null;
    avg_daily_volume?: number | null;
    settlement_date?: string | null;
    provider?: string;
    endpoint?: string;
    collected_at?: string;
  };
  options_flow?: {
    put_call_volume_ratio?: number | null;
    put_call_open_interest_ratio?: number | null;
    total_call_volume?: number | null;
    total_put_volume?: number | null;
    total_call_open_interest?: number | null;
    total_put_open_interest?: number | null;
    contract_count?: number | null;
    iv_coverage_pct?: number | null;
    greeks_coverage_pct?: number | null;
    provider?: string;
    endpoint?: string;
    collected_at?: string;
  };
  corporate_actions?: {
    dividend_events?: number | null;
    latest_dividend_date?: string | null;
    split_events?: number | null;
    latest_split_date?: string | null;
  };
  flags?: string[];
  signal_score?: number | null;
  sources?: string[];
}

export interface SymbolDetail {
  symbol: string;
  as_of_date: string;
  spot_price: number | null;
  expected_move?: ExpectedMove;
  straddle_features: Straddle[];
  earnings_history?: Array<{
    date: string;
    timing: string;
    /** Quarter label from the provider's fiscal year and quarter when available. */
    q?: string;
    fiscal_year?: number | null;
    fiscal_q?: string | null;
    /** Implied move at the time, as a decimal fraction. */
    implied?: number | null;
    implied_as_of?: string | null;
    implied_expiration?: string | null;
    implied_dte?: number | null;
    implied_lead_days?: number | null;
    implied_atm_strike?: number | null;
    implied_straddle_abs?: number | null;
    implied_atm_iv?: number | null;
    implied_quality_status?: 'decision_eligible_eod' | null;
    /** Realized close-to-close move, as a signed decimal fraction. */
    actual?: number | null;
    eps_actual?: number | null;
    eps_estimate?: number | null;
    eps_surprise_pct?: number | null;
    revenue_actual?: number | null;
    revenue_estimate?: number | null;
    rev_surprise_pct?: number | null;
  }>;
  next_earnings?: string | null;
  next_earnings_timing?: string;
  vol_regime?: VolRegime | null;
  provider_enrichment?: ProviderEnrichment | null;
  short_days_to_cover?: number | null;
  put_call_volume_ratio?: number | null;
  put_call_open_interest_ratio?: number | null;
  provider_signal_score?: number | null;
}

export interface LivePrice {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  updated: string | null;
  source: 'finnhub' | 'alpaca_iex' | 'polygon_grouped' | 'mixed' | 'unavailable';
  session?: 'premarket' | 'regular' | 'afterhours' | 'delayed' | 'closed';
  marketOpen: boolean;
}

export type PredictionMode = 'snapshot' | 'spot_updated';
export type LivePredictionStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

export interface LivePredictionResponse {
  symbol: string;
  horizon_days: number;
  em_ml_pct: number;
  em_ml_abs: number;
  quantiles: Record<string, number>;
  spot_used: number;
  feature_snapshot_date: string | null;
  earnings_date: string | null;
  source: 'computed' | 'cached' | 'nightly_fallback';
  inference_mode?: 'snapshot_rescore' | 'spot_updated_snapshot' | 'nightly_snapshot';
  market_data_mode?: 'end_of_day';
  decision_scope?: 'end_of_day_research';
  live_trading_eligible?: false;
  updated_inputs?: Array<'spot' | 'spot_for_dollar_scaling'>;
  fallback_kind?: 'static_ml' | 'straddle';
  fallback_reason?: string;
  served_at: string;
  snapshot_age_days?: number | null;
  forecast_scored_at?: string | null;
  model_version?: string | null;
  model_trained_at?: string | null;
  model_loaded_at?: string | null;
  feature_schema_hash?: string | null;
}

export interface LivePredictionState {
  status: LivePredictionStatus;
  key: string | null;
  response: LivePredictionResponse | null;
  error: string | null;
  updatedAt: number;
}

export interface IntradaySeries {
  symbol: string;
  bars: { t: string; c: number }[];
  previousClose: number | null;
  asOf: string | null;
  sessionDate: string | null;
  isCurrentSession: boolean;
}
