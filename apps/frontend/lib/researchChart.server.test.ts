import { describe, expect, it } from 'vitest';
import type { CohortEvent } from './researchCohort';
import {
  CALIBRATION_CHART_MAX_POINTS,
  sampleCalibrationEvents,
} from './researchChart.server';

function event(index: number): CohortEvent {
  const ticker = `T${String(index).padStart(4, '0')}`;
  const day = String((index % 28) + 1).padStart(2, '0');
  const month = String((Math.floor(index / 28) % 12) + 1).padStart(2, '0');
  const implied = 0.03 + (index % 20) / 1000;
  const realized = 0.02 + (index % 30) / 1000;
  return {
    ticker,
    date: `2025-${month}-${day}`,
    timing: index % 2 ? 'after_market_close' : 'before_market_open',
    fiscal_q: 'Q1',
    actual: realized,
    realized_abs: realized,
    implied,
    implied_as_of: '2025-01-01',
    implied_expiration: '2025-12-31',
    implied_dte: 2,
    implied_lead_days: 1,
    implied_atm_strike: 100,
    implied_atm_iv: 0.4,
    implied_quality_status: 'decision_eligible_eod',
    eps_surprise_pct: null,
    rev_surprise_pct: null,
    edge: realized - implied,
    ratio: realized / implied,
    outside_implied: realized > implied,
  };
}

function identities(events: CohortEvent[]): string[] {
  return events.map((row) => `${row.ticker}|${row.date}`);
}

describe('sampleCalibrationEvents', () => {
  it('is invariant to caller ordering and caps the chart population', () => {
    const population = Array.from({ length: 500 }, (_, index) => event(index));
    const forward = sampleCalibrationEvents(population);
    const reversed = sampleCalibrationEvents([...population].reverse());

    expect(forward).toHaveLength(CALIBRATION_CHART_MAX_POINTS);
    expect(identities(reversed)).toEqual(identities(forward));
    expect(new Set(identities(forward)).size).toBe(CALIBRATION_CHART_MAX_POINTS);
  });

  it('returns the complete population when it is below the cap', () => {
    const population = Array.from({ length: 12 }, (_, index) => event(index));
    const sample = sampleCalibrationEvents(population);

    expect(sample).toHaveLength(population.length);
    expect(new Set(identities(sample))).toEqual(new Set(identities(population)));
  });
});
