import { createHash } from 'node:crypto';
import type { CohortEvent } from './researchCohort';

export const CALIBRATION_CHART_MAX_POINTS = 350;
export const CALIBRATION_CHART_SAMPLING = 'sha256_event_identity_lowest_v1' as const;

function eventIdentity(event: Pick<CohortEvent, 'ticker' | 'date'>): string {
  return `${event.ticker}|${event.date}`;
}

function eventRank(identity: string): string {
  return createHash('sha256').update(identity).digest('hex');
}

export function sampleCalibrationEvents(
  events: CohortEvent[],
  maxPoints = CALIBRATION_CHART_MAX_POINTS,
): CohortEvent[] {
  const limit = Math.max(1, Math.floor(maxPoints));
  return events
    .map((event) => {
      const identity = eventIdentity(event);
      return { event, identity, rank: eventRank(identity) };
    })
    .sort((left, right) =>
      left.rank.localeCompare(right.rank) || left.identity.localeCompare(right.identity),
    )
    .slice(0, limit)
    .map(({ event }) => event);
}
