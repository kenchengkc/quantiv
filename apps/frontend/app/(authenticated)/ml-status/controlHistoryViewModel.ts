import type { ControlHistory, ControlHistoryRun } from './controlPlaneTypes';

export type ControlReleaseComparison = {
  current: ControlHistoryRun | null;
  previous: ControlHistoryRun | null;
  coverageDeltaPp: number | null;
  missingEventsDelta: number | null;
  decisionAvailabilityDeltaPp: number | null;
  criticalFeaturesDelta: number | null;
  newExceptionCodes: string[];
  resolvedExceptionCodes: string[];
};

function difference(
  current: number | null,
  previous: number | null,
): number | null {
  return current == null || previous == null ? null : current - previous;
}

export function decisionAvailability(
  run: ControlHistoryRun,
): number | null {
  const rejection =
    run.decision_group_rejection_rate ?? run.contract_rejection_rate;
  return rejection == null ? null : 1 - rejection;
}

export function compareControlReleases(
  history: ControlHistory,
): ControlReleaseComparison {
  const current = history.runs[0] ?? null;
  const previous = history.runs[1] ?? null;
  if (!current || !previous) {
    return {
      current,
      previous,
      coverageDeltaPp: null,
      missingEventsDelta: null,
      decisionAvailabilityDeltaPp: null,
      criticalFeaturesDelta: null,
      newExceptionCodes: [],
      resolvedExceptionCodes: [],
    };
  }

  const currentCodes = new Set(current.exception_codes);
  const previousCodes = new Set(previous.exception_codes);
  const coverageDelta = difference(
    current.event_coverage_pct,
    previous.event_coverage_pct,
  );
  const availabilityDelta = difference(
    decisionAvailability(current),
    decisionAvailability(previous),
  );
  return {
    current,
    previous,
    coverageDeltaPp: coverageDelta == null ? null : coverageDelta * 100,
    missingEventsDelta: difference(
      current.missing_events,
      previous.missing_events,
    ),
    decisionAvailabilityDeltaPp:
      availabilityDelta == null ? null : availabilityDelta * 100,
    criticalFeaturesDelta: difference(
      current.critical_features,
      previous.critical_features,
    ),
    newExceptionCodes: current.exception_codes.filter(
      (code) => !previousCodes.has(code),
    ),
    resolvedExceptionCodes: previous.exception_codes.filter(
      (code) => !currentCodes.has(code),
    ),
  };
}

export function recentControlRuns(
  history: ControlHistory,
  limit = 7,
): ControlHistoryRun[] {
  return history.runs.slice(0, Math.max(1, limit)).reverse();
}
