export type CohortTiming = 'all' | 'bmo' | 'amc';
export type CohortOutcome = 'all' | 'inside' | 'outside';
export type CohortEps = 'all' | 'beat' | 'miss';
export type CohortSort = 'date' | 'ticker' | 'implied' | 'realized' | 'edge' | 'ratio' | 'eps';
export type SortDir = 'asc' | 'desc';

export interface CohortEvent {
  ticker: string;
  date: string;
  timing: string;
  fiscal_q: string | null;
  actual: number;
  realized_abs: number;
  implied: number;
  implied_as_of: string;
  implied_expiration: string | null;
  implied_dte: number | null;
  implied_lead_days: number | null;
  implied_atm_strike: number | null;
  implied_atm_iv: number | null;
  implied_quality_status: 'decision_eligible_eod';
  eps_surprise_pct: number | null;
  rev_surprise_pct: number | null;
  edge: number;
  ratio: number;
  outside_implied: boolean;
}

export interface CohortQuery {
  q: string;
  timing: CohortTiming;
  quarter: 'all' | 'Q1' | 'Q2' | 'Q3' | 'Q4';
  outcome: CohortOutcome;
  eps: CohortEps;
  minImplied: number | null;
  maxImplied: number | null;
  minLead: number | null;
  maxLead: number | null;
  sort: CohortSort;
  dir: SortDir;
  limit: number;
}

export interface CohortSummary {
  events: number;
  symbols: number;
  avgImplied: number | null;
  avgRealized: number | null;
  medianImplied: number | null;
  medianRealized: number | null;
  medianRatio: number | null;
  outsideRate: number | null;
  meanAbsoluteError: number | null;
  avgSignedMove: number | null;
  avgEpsSurprise: number | null;
  ratioP25: number | null;
  ratioP75: number | null;
}

const SORTS: readonly CohortSort[] = [
  'date',
  'ticker',
  'implied',
  'realized',
  'edge',
  'ratio',
  'eps',
] as const;

function finiteNumber(value: string | null): number | null {
  if (value == null || value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function boundedLimit(value: string | null): number {
  const parsed = Number(value ?? '250');
  if (!Number.isFinite(parsed)) return 250;
  return Math.max(1, Math.min(1000, Math.floor(parsed)));
}

export function parseCohortQuery(params: URLSearchParams): CohortQuery {
  const timingRaw = params.get('timing');
  const quarterRaw = params.get('quarter');
  const outcomeRaw = params.get('outcome');
  const epsRaw = params.get('eps');
  const sortRaw = params.get('sort');
  const dirRaw = params.get('dir');

  return {
    q: (params.get('q') ?? '').trim().toUpperCase().slice(0, 12),
    timing: timingRaw === 'bmo' || timingRaw === 'amc' ? timingRaw : 'all',
    quarter:
      quarterRaw === 'Q1' || quarterRaw === 'Q2' || quarterRaw === 'Q3' || quarterRaw === 'Q4'
        ? quarterRaw
        : 'all',
    outcome:
      outcomeRaw === 'inside' || outcomeRaw === 'outside' ? outcomeRaw : 'all',
    eps: epsRaw === 'beat' || epsRaw === 'miss' ? epsRaw : 'all',
    minImplied: finiteNumber(params.get('minImplied')),
    maxImplied: finiteNumber(params.get('maxImplied')),
    minLead: finiteNumber(params.get('minLead')),
    maxLead: finiteNumber(params.get('maxLead')),
    sort: (SORTS as readonly string[]).includes(sortRaw ?? '')
      ? (sortRaw as CohortSort)
      : 'date',
    dir: dirRaw === 'asc' ? 'asc' : 'desc',
    limit: boundedLimit(params.get('limit')),
  };
}

export function canonicalCohortQuery(query: CohortQuery): Record<string, unknown> {
  return {
    q: query.q,
    timing: query.timing,
    quarter: query.quarter,
    outcome: query.outcome,
    eps: query.eps,
    min_implied: query.minImplied,
    max_implied: query.maxImplied,
    min_lead_days: query.minLead,
    max_lead_days: query.maxLead,
    sort: query.sort,
    dir: query.dir,
    limit: query.limit,
  };
}

function timingBucket(value: string): 'bmo' | 'amc' | 'other' {
  const normalized = value.toLowerCase();
  if (normalized.includes('before') || normalized === 'bmo') return 'bmo';
  if (normalized.includes('after') || normalized === 'amc') return 'amc';
  return 'other';
}

function sortValue(event: CohortEvent, key: CohortSort): number | string | null {
  switch (key) {
    case 'date':
      return Date.parse(event.date);
    case 'ticker':
      return event.ticker;
    case 'implied':
      return event.implied;
    case 'realized':
      return event.realized_abs;
    case 'edge':
      return event.edge;
    case 'ratio':
      return event.ratio;
    case 'eps':
      return event.eps_surprise_pct;
  }
}

export function applyCohortQuery(events: CohortEvent[], query: CohortQuery): CohortEvent[] {
  const filtered = events.filter((event) => {
    if (query.q && !event.ticker.includes(query.q)) return false;
    if (query.timing !== 'all' && timingBucket(event.timing) !== query.timing) return false;
    if (query.quarter !== 'all' && event.fiscal_q !== query.quarter) return false;
    if (query.outcome === 'inside' && event.outside_implied) return false;
    if (query.outcome === 'outside' && !event.outside_implied) return false;
    if (query.eps === 'beat' && !(event.eps_surprise_pct != null && event.eps_surprise_pct > 0)) return false;
    if (query.eps === 'miss' && !(event.eps_surprise_pct != null && event.eps_surprise_pct < 0)) return false;
    if (query.minImplied != null && event.implied < query.minImplied) return false;
    if (query.maxImplied != null && event.implied > query.maxImplied) return false;
    if (query.minLead != null && (event.implied_lead_days == null || event.implied_lead_days < query.minLead)) return false;
    if (query.maxLead != null && (event.implied_lead_days == null || event.implied_lead_days > query.maxLead)) return false;
    return true;
  });

  const direction = query.dir === 'asc' ? 1 : -1;
  filtered.sort((a, b) => {
    const left = sortValue(a, query.sort);
    const right = sortValue(b, query.sort);
    if (left == null && right == null) return a.ticker.localeCompare(b.ticker);
    if (left == null) return 1;
    if (right == null) return -1;
    if (typeof left === 'string' && typeof right === 'string') {
      const cmp = left.localeCompare(right);
      return cmp === 0 ? a.date.localeCompare(b.date) * -direction : cmp * direction;
    }
    const l = Number(left);
    const r = Number(right);
    if (l === r) return a.ticker.localeCompare(b.ticker);
    return (l < r ? -1 : 1) * direction;
  });

  return filtered.slice(0, query.limit);
}

function average(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function quantile(values: number[], q: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = (sorted.length - 1) * q;
  const lo = Math.floor(index);
  const hi = Math.ceil(index);
  if (lo === hi) return sorted[lo];
  const weight = index - lo;
  return sorted[lo] * (1 - weight) + sorted[hi] * weight;
}

export function summarizeCohort(events: CohortEvent[]): CohortSummary {
  const implied = events.map((event) => event.implied);
  const realized = events.map((event) => event.realized_abs);
  const ratios = events.map((event) => event.ratio).filter(Number.isFinite);
  const eps = events
    .map((event) => event.eps_surprise_pct)
    .filter((value): value is number => value != null && Number.isFinite(value));

  return {
    events: events.length,
    symbols: new Set(events.map((event) => event.ticker)).size,
    avgImplied: average(implied),
    avgRealized: average(realized),
    medianImplied: quantile(implied, 0.5),
    medianRealized: quantile(realized, 0.5),
    medianRatio: quantile(ratios, 0.5),
    outsideRate: events.length
      ? events.filter((event) => event.outside_implied).length / events.length
      : null,
    meanAbsoluteError: average(events.map((event) => Math.abs(event.realized_abs - event.implied))),
    avgSignedMove: average(events.map((event) => event.actual)),
    avgEpsSurprise: average(eps),
    ratioP25: quantile(ratios, 0.25),
    ratioP75: quantile(ratios, 0.75),
  };
}
