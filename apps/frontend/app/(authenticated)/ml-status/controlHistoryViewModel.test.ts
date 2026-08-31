import { describe, expect, it } from 'vitest';
import {
  compareControlReleases,
  recentControlRuns,
} from './controlHistoryViewModel';
import type { ControlHistory, ControlHistoryRun } from './controlPlaneTypes';

function run(
  generatedAt: string,
  overrides: Partial<ControlHistoryRun> = {},
): ControlHistoryRun {
  return {
    generated_at: generatedAt,
    status: 'degraded',
    publication_eligible: true,
    source_date: '2026-08-28',
    source_session_lag: 0,
    event_coverage_pct: 0.8,
    expected_events: 20,
    covered_events: 16,
    missing_events: 4,
    contract_rejection_rate: 0.4,
    pair_rejection_rate: 0.8,
    duplicate_rows: 0,
    model_snapshot_date: '2026-08-28',
    model_status: 'degraded',
    drift_status: 'warning',
    critical_features: 2,
    warning_features: 4,
    challenger_present: false,
    outcome_status: 'unavailable',
    critical_exceptions: 0,
    warning_exceptions: 1,
    exception_codes: ['missing_chain'],
    workflow: null,
    ...overrides,
  };
}

describe('control release history', () => {
  it('compares the latest accepted refresh with its predecessor', () => {
    const history: ControlHistory = {
      schema: 'quantiv.control-plane-history.v1',
      generated_at: '2026-08-30T12:00:00Z',
      runs: [
        run('2026-08-30T12:00:00Z', {
          event_coverage_pct: 0.85,
          missing_events: 3,
          contract_rejection_rate: 0.42,
          critical_features: 1,
          exception_codes: ['new_warning'],
        }),
        run('2026-08-29T12:00:00Z', {
          exception_codes: ['missing_chain'],
        }),
      ],
    };

    const comparison = compareControlReleases(history);
    expect(comparison).toMatchObject({
      missingEventsDelta: -1,
      criticalFeaturesDelta: -1,
      newExceptionCodes: ['new_warning'],
      resolvedExceptionCodes: ['missing_chain'],
    });
    expect(comparison.coverageDeltaPp).toBeCloseTo(5);
    expect(comparison.rejectionDeltaPp).toBeCloseTo(2);
    expect(recentControlRuns(history).map((item) => item.generated_at)).toEqual([
      '2026-08-29T12:00:00Z',
      '2026-08-30T12:00:00Z',
    ]);
  });

  it('reports no false deltas before a second retained refresh', () => {
    const history: ControlHistory = {
      schema: 'quantiv.control-plane-history.v1',
      generated_at: '2026-08-30T12:00:00Z',
      runs: [run('2026-08-30T12:00:00Z')],
    };

    expect(compareControlReleases(history).previous).toBeNull();
    expect(compareControlReleases(history).coverageDeltaPp).toBeNull();
  });
});
