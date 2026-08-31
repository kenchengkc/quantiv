import type { ControlHistory, ControlHistoryRun } from './controlPlaneTypes';

export type ControlReleaseComparison = {
  current: ControlHistoryRun | null;
  previous: ControlHistoryRun | null;
  coverageDeltaPp: number | null;
  missingEventsDelta: number | null;
  rejectionDeltaPp: number | null;
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
      rejectionDeltaPp: null,
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
  const rejectionDelta = difference(
    current.contract_rejection_rate,
    previous.contract_rejection_rate,
  );
  return {
    current,
    previous,
    coverageDeltaPp: coverageDelta == null ? null : coverageDelta * 100,
    missingEventsDelta: difference(
      current.missing_events,
      previous.missing_events,
    ),
    rejectionDeltaPp: rejectionDelta == null ? null : rejectionDelta * 100,
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
