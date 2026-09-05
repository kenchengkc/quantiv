import {
  applyCohortQuery,
  parseCohortQuery,
  summarizeCohort,
  type CohortEvent,
} from './researchCohort';
import { readPublicJson } from './researchSnapshot.server';
import {
  buildComparableResearchDefinition,
  type ComparableResearchContext,
} from './comparableResearch';

type HistoricalUniverse = {
  events?: CohortEvent[];
};

/**
 * Resolve the linked Research Lab cohort and summarize the full matching set.
 * This reads the deterministic prebuilt history aggregate from disk; it makes
 * no provider, database, Redis, or internal HTTP request.
 */
export function buildComparableResearchContext(
  payload: unknown,
): ComparableResearchContext | null {
  const definition = buildComparableResearchDefinition(payload);
  if (!definition) return null;

  const universe = readPublicJson<HistoricalUniverse>('research-history.json');
  const rows = Array.isArray(universe?.events) ? universe.events : [];
  if (rows.length === 0) {
    return { ...definition, summary: null };
  }

  const query = parseCohortQuery(new URLSearchParams(definition.queryString));
  const matching = applyCohortQuery(rows, {
    ...query,
    limit: Math.max(1, rows.length),
  });
  const summary = summarizeCohort(matching);

  return {
    ...definition,
    summary: {
      events: summary.events,
      symbols: summary.symbols,
      medianImplied: summary.medianImplied,
      medianRealized: summary.medianRealized,
      medianRatio: summary.medianRatio,
      outsideRate: summary.outsideRate,
    },
  };
}
