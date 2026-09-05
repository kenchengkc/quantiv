import type { CohortSummary } from './researchCohort';

export type ComparablePayload = {
  expected_move?: {
    straddle_pct?: number | null;
    timing?: string | null;
  } | null;
  next_earnings_timing?: string | null;
};

export type ComparableTiming = 'all' | 'bmo' | 'amc';

export type ComparableResearchDefinition = {
  href: string;
  queryString: string;
  currentImplied: number;
  minImplied: number;
  maxImplied: number;
  timing: ComparableTiming;
};

export type ComparableResearchContext = ComparableResearchDefinition & {
  summary: Pick<
    CohortSummary,
    | 'events'
    | 'symbols'
    | 'medianImplied'
    | 'medianRealized'
    | 'medianRatio'
    | 'outsideRate'
  > | null;
};

function timingBucket(value: string | null | undefined): ComparableTiming {
  const normalized = (value ?? '').toLowerCase();
  if (normalized.includes('before') || normalized === 'bmo') return 'bmo';
  if (normalized.includes('after') || normalized === 'amc') return 'amc';
  return 'all';
}

/**
 * Build the deliberately simple comparable-event definition used throughout
 * the product: same BMO/AMC session when known and a +/-25% band around the
 * current event's static straddle-implied move.
 */
export function buildComparableResearchDefinition(
  payload: unknown,
): ComparableResearchDefinition | null {
  if (!payload || typeof payload !== 'object') return null;
  const research = payload as ComparablePayload;
  const implied = research.expected_move?.straddle_pct;
  if (typeof implied !== 'number' || !Number.isFinite(implied) || implied <= 0) {
    return null;
  }

  const timing = timingBucket(
    research.expected_move?.timing ?? research.next_earnings_timing,
  );
  const minImplied = Number((implied * 0.75).toFixed(6));
  const maxImplied = Number((implied * 1.25).toFixed(6));
  const params = new URLSearchParams({
    minImplied: String(minImplied),
    maxImplied: String(maxImplied),
    sort: 'ratio',
    dir: 'desc',
    limit: '100',
  });
  if (timing !== 'all') params.set('timing', timing);
  const queryString = params.toString();

  return {
    href: `/research?${queryString}`,
    queryString,
    currentImplied: implied,
    minImplied,
    maxImplied,
    timing,
  };
}
