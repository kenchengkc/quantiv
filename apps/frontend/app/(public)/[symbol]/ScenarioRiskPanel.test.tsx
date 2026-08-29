import { describe, expect, it } from 'vitest';
import { buildScenarioRows } from './ScenarioRiskPanel';

describe('buildScenarioRows', () => {
  it('calculates contract P&L from intrinsic value less premium', () => {
    const rows = buildScenarioRows(100, 100, 5, 0.1);

    expect(rows).toHaveLength(5);
    expect(rows[2].pnlPerShare).toBe(-5);
    expect(rows[4].pnlPerContract).toBeCloseTo(1000, 8);
    expect(rows[0].pnlPerContract).toBeCloseTo(1000, 8);
  });

  it('rejects incomplete or non-positive scenario inputs', () => {
    expect(buildScenarioRows(100, 100, 5, 0)).toEqual([]);
    expect(buildScenarioRows(0, 100, 5, 0.1)).toEqual([]);
  });
});
