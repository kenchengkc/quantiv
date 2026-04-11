// Shared TypeScript types for Quantiv

export interface OptionContract {
  symbol: string;
  strike: number;
  expiry: Date;
  type: 'call' | 'put';
  bid?: number;
  ask?: number;
  mid?: number;
  iv?: number;
  delta?: number;
  gamma?: number;
  theta?: number;
  vega?: number;
  volume?: number;
  openInterest?: number;
}

export interface OptionsChain {
  symbol: string;
  expiry: Date;
  spot: number;
  calls: OptionContract[];
  puts: OptionContract[];
}

export interface ExpectedMove {
  symbol: string;
  expiry: Date;
  movePercent: number;
  moveAbsolute: number;
  confidence: 'high' | 'medium' | 'low';
}

export interface ExpectedMoveForecast {
  underlying: string;
  quote_ts: string;
  exp_date: string;
  horizon: string;
  em_baseline: number;
  band68_low: number;
  band68_high: number;
  band95_low?: number;
  band95_high?: number;
}

export interface LiveMarketData {
  symbol: string;
  price: number;
  change: number;
  change_percent: number;
  volume: number;
  timestamp: string;
}

export interface IVStats {
  rank: number;
  percentile: number;
  current: number;
  min: number;
  max: number;
  mean: number;
  median: number;
  stdDev: number;
  daysInSample: number;
}

export interface EarningsEvent {
  date: string;
  confidence: 'confirmed' | 'estimated';
  timing?: 'bmo' | 'amc' | 'unknown';
}

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  services: Record<string, string>;
}

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  detail?: string;
  timestamp: string;
}
