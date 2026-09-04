import sp500Constituents from '../../../lib/data/sp500-constituents.json';

const SP500_SET = new Set(
  (sp500Constituents as { symbol: string }[]).map((row) => row.symbol),
);

export type ScreenerPreset =
  | 'rich_vol'
  | 'cheap_vol'
  | 'big_movers'
  | 'confident'
  | 'crowded'
  | null;

export type ScreenerSortKey =
  | 'name'
  | 'edge'
  | 'dte'
  | 'date'
  | 'straddle'
  | 'ml'
  | 'iv'
  | 'band'
  | 'skew'
  | 'spot'
  | 'iv_rank'
  | 'hist_avg'
  | 'hist_edge'
  | 'iv_crush'
  | 'short'
  | 'flow';

export type ScreenerSortDir = 'asc' | 'desc';
export type ScreenerTiming = 'all' | 'bmo' | 'amc';

export type ResearchScreenerEvent = Record<string, unknown> & {
  ticker: string;
  earnings_date: string;
  timing?: string | null;
  spot_price?: number | null;
  atm_iv?: number | null;
  em_straddle_pct?: number | null;
  em_ml_pct?: number | null;
  em_method?: string | null;
  lead_time_days?: number | null;
  skew_atm?: number | null;
  p10?: number | null;
  p90?: number | null;
  iv_rank?: number | null;
  hist_move_avg_4q?: number | null;
  iv_crush_pct?: number | null;
  short_days_to_cover?: number | null;
  put_call_volume_ratio?: number | null;
  provider_enrichment?: {
    short_interest?: { days_to_cover?: number | null } | null;
    options_flow?: { put_call_volume_ratio?: number | null } | null;
  } | null;
};

export type ScreenerResearchQuery = {
  q: string;
  sp500: boolean;
  minSpot: number;
  timing: ScreenerTiming;
  mlOnly: boolean;
  preset: ScreenerPreset;
  sort: ScreenerSortKey;
  dir: ScreenerSortDir;
};

const SORT_KEYS: readonly ScreenerSortKey[] = [
  'name',
  'edge',
  'dte',
  'date',
  'straddle',
  'ml',
  'iv',
  'band',
  'skew',
  'spot',
  'iv_rank',
  'hist_avg',
  'hist_edge',
  'iv_crush',
  'short',
  'flow',
] as const;

const PRESETS: readonly Exclude<ScreenerPreset, null>[] = [
  'rich_vol',
  'cheap_vol',
  'big_movers',
  'confident',
  'crowded',
] as const;

function timingBucket(value?: string | null): 'bmo' | 'amc' | 'dmh' | 'unknown' {
  const key = (value || '').toLowerCase();
  if (key.includes('before') || key === 'bmo') return 'bmo';
  if (key.includes('after') || key === 'amc') return 'amc';
  if (key.includes('during') || key === 'dmh') return 'dmh';
  return 'unknown';
}

function finite(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) ? value : null;
}

function histEdge(event: ResearchScreenerEvent): number | null {
  const straddle = finite(event.em_straddle_pct);
  const history = finite(event.hist_move_avg_4q);
  if (straddle == null || history == null || history === 0) return null;
  return (straddle - history) / history;
}

function edge(event: ResearchScreenerEvent): number | null {
  const straddle = finite(event.em_straddle_pct);
  const model = finite(event.em_ml_pct);
  return straddle == null || model == null ? null : straddle - model;
}

function band80(event: ResearchScreenerEvent): number | null {
  const low = finite(event.p10);
  const high = finite(event.p90);
  return low == null || high == null ? null : high - low;
}

function shortDays(event: ResearchScreenerEvent): number | null {
  return finite(
    event.short_days_to_cover ??
      event.provider_enrichment?.short_interest?.days_to_cover ??
      null,
  );
}

function putCallVolumeRatio(event: ResearchScreenerEvent): number | null {
  return finite(
    event.put_call_volume_ratio ??
      event.provider_enrichment?.options_flow?.put_call_volume_ratio ??
      null,
  );
}

function flowImbalance(event: ResearchScreenerEvent): number | null {
  const ratio = putCallVolumeRatio(event);
  if (ratio == null || ratio <= 0) return null;
  return Math.abs(Math.log(ratio));
}

export function parseScreenerResearchQuery(
  searchParams: URLSearchParams,
): ScreenerResearchQuery {
  const minSpotRaw = Number(searchParams.get('minSpot') ?? '15');
  const timingRaw = searchParams.get('timing');
  const presetRaw = searchParams.get('preset');
  const sortRaw = searchParams.get('sort');

  return {
    q: (searchParams.get('q') ?? '').trim().toUpperCase(),
    sp500: searchParams.get('sp500') === '1',
    minSpot: Number.isFinite(minSpotRaw) && minSpotRaw > 0 ? minSpotRaw : 15,
    timing: timingRaw === 'bmo' || timingRaw === 'amc' ? timingRaw : 'all',
    mlOnly: searchParams.get('ml') === '1',
    preset: (PRESETS as readonly string[]).includes(presetRaw ?? '')
      ? (presetRaw as Exclude<ScreenerPreset, null>)
      : null,
    sort: (SORT_KEYS as readonly string[]).includes(sortRaw ?? '')
      ? (sortRaw as ScreenerSortKey)
      : 'hist_edge',
    dir: searchParams.get('dir') === 'asc' ? 'asc' : 'desc',
  };
}

export function canonicalScreenerQuery(query: ScreenerResearchQuery) {
  return {
    q: query.q,
    sp500: query.sp500,
    min_spot: query.minSpot,
    timing: query.timing,
    ml_only: query.mlOnly,
    preset: query.preset,
    sort: query.sort,
    direction: query.dir,
  };
}

export function applyScreenerResearchQuery(
  input: ResearchScreenerEvent[],
  query: ScreenerResearchQuery,
): ResearchScreenerEvent[] {
  const filtered = input.filter((event) => {
    if (query.q && !event.ticker.includes(query.q)) return false;
    if (query.sp500 && !SP500_SET.has(event.ticker)) return false;
    if ((finite(event.spot_price) ?? 0) < query.minSpot) return false;

    if (query.timing !== 'all') {
      if (timingBucket(event.timing) !== query.timing) return false;
    }
    if (query.mlOnly && event.em_method !== 'ml_lightgbm') return false;

    if (query.preset === 'rich_vol') {
      const value = histEdge(event);
      if (value == null || value < 0.2) return false;
    } else if (query.preset === 'cheap_vol') {
      const value = finite(event.iv_rank);
      if (value == null || value > 0.3) return false;
    } else if (query.preset === 'big_movers') {
      if ((finite(event.em_straddle_pct) ?? 0) < 0.1) return false;
    } else if (query.preset === 'confident') {
      const width = band80(event);
      if (width == null || width > 0.08) return false;
    } else if (query.preset === 'crowded') {
      const days = shortDays(event);
      const flow = flowImbalance(event);
      if ((days == null || days < 3) && (flow == null || flow < 0.35)) return false;
    }

    return true;
  });

  const direction = query.dir === 'asc' ? 1 : -1;
  const value = (event: ResearchScreenerEvent): number | null => {
    switch (query.sort) {
      case 'edge':
        return edge(event);
      case 'dte':
        return finite(event.lead_time_days);
      case 'date': {
        const timestamp = new Date(event.earnings_date).getTime();
        return Number.isFinite(timestamp) ? timestamp : null;
      }
      case 'straddle':
        return finite(event.em_straddle_pct);
      case 'ml':
        return finite(event.em_ml_pct);
      case 'iv':
        return finite(event.atm_iv);
      case 'band':
        return band80(event);
      case 'skew':
        return finite(event.skew_atm);
      case 'spot':
        return finite(event.spot_price);
      case 'iv_rank':
        return finite(event.iv_rank);
      case 'hist_avg':
        return finite(event.hist_move_avg_4q);
      case 'hist_edge':
        return histEdge(event);
      case 'iv_crush':
        return finite(event.iv_crush_pct);
      case 'short':
        return shortDays(event);
      case 'flow':
        return flowImbalance(event);
      default:
        return 0;
    }
  };

  return [...filtered].sort((left, right) => {
    if (query.sort === 'name') {
      return left.ticker.localeCompare(right.ticker) * direction;
    }
    const a = value(left);
    const b = value(right);
    if (a == null && b == null) return left.ticker.localeCompare(right.ticker);
    if (a == null) return 1;
    if (b == null) return -1;
    if (a === b) return left.ticker.localeCompare(right.ticker);
    return a < b ? -direction : direction;
  });
}
