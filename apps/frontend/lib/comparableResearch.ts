import type { CohortSummary } from './researchCohort';

export type ComparablePayload = {
  expected_move?: {
    straddle_pct?: number | null;
    timing?: string | null;
    lead_time_days?: number | null;
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
  currentLeadDays: number | null;
  minLead: number | null;
  maxLead: number | null;
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

function leadWindow(value: number | null | undefined): {
  currentLeadDays: number | null;
  minLead: number | null;
  maxLead: number | null;
} {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return { currentLeadDays: null, minLead: null, maxLead: null };
  }

  const lead = Math.round(value);
  if (lead <= 1) return { currentLeadDays: lead, minLead: 0, maxLead: 1 };
  if (lead <= 3) return { currentLeadDays: lead, minLead: 2, maxLead: 3 };
  if (lead <= 7) return { currentLeadDays: lead, minLead: 4, maxLead: 7 };
  if (lead <= 14) return { currentLeadDays: lead, minLead: 8, maxLead: 14 };

  // Historical option evidence is intentionally selected from the final
  // 14 calendar days before each event. Do not pretend longer-dated current
  // observations have a like-for-like lead-time cohort when that evidence
  // boundary cannot support one.
  return { currentLeadDays: lead, minLead: null, maxLead: null };
}

/**
 * Build the transparent comparable-event definition used throughout the
 * product: same BMO/AMC session when known, a +/-25% band around the current
 * static straddle-implied move, and the same pre-event lead-time bucket when
 * the historical evidence contract supports it (T0-1, T2-3, T4-7, T8-14).
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
  const { currentLeadDays, minLead, maxLead } = leadWindow(
    research.expected_move?.lead_time_days,
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
  if (minLead != null && maxLead != null) {
    params.set('minLead', String(minLead));
    params.set('maxLead', String(maxLead));
  }
  const queryString = params.toString();

  return {
    href: `/research?${queryString}`,
    queryString,
    currentImplied: implied,
    minImplied,
    maxImplied,
    timing,
    currentLeadDays,
    minLead,
    maxLead,
  };
}
