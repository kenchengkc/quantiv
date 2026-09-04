import { describe, expect, it } from 'vitest';
import { buildComparableResearchDefinition } from './comparableResearch';

describe('comparable research definition', () => {
  it('uses the current straddle move, AMC session, and T0-1 lead bucket transparently', () => {
    const result = buildComparableResearchDefinition({
      expected_move: {
        straddle_pct: 0.0982,
        timing: 'after_market_close',
        lead_time_days: 1,
      },
    });

    expect(result).not.toBeNull();
    expect(result).toMatchObject({
      currentImplied: 0.0982,
      minImplied: 0.07365,
      maxImplied: 0.12275,
      timing: 'amc',
      currentLeadDays: 1,
      minLead: 0,
      maxLead: 1,
    });
    expect(result?.href).toContain('/research?');
    expect(result?.href).toContain('timing=amc');
    expect(result?.href).toContain('minLead=0');
    expect(result?.href).toContain('maxLead=1');
    expect(result?.href).toContain('sort=ratio');
    expect(result?.href).toContain('dir=desc');
  });

  it('falls back to the symbol-level timing when the expected-move object omits it', () => {
    const result = buildComparableResearchDefinition({
      expected_move: { straddle_pct: 0.04, lead_time_days: 3 },
      next_earnings_timing: 'before_market_open',
    });

    expect(result?.timing).toBe('bmo');
    expect(result?.href).toContain('timing=bmo');
    expect(result?.minImplied).toBe(0.03);
    expect(result?.maxImplied).toBe(0.05);
    expect(result?.minLead).toBe(2);
    expect(result?.maxLead).toBe(3);
  });

  it.each([
    [0, 0, 1],
    [1, 0, 1],
    [2, 2, 3],
    [3, 2, 3],
    [4, 4, 7],
    [7, 4, 7],
    [8, 8, 14],
    [14, 8, 14],
  ])('maps lead day %s into the documented %s-%s bucket', (lead, minLead, maxLead) => {
    const result = buildComparableResearchDefinition({
      expected_move: { straddle_pct: 0.06, lead_time_days: lead },
    });
    expect(result?.minLead).toBe(minLead);
    expect(result?.maxLead).toBe(maxLead);
  });

  it('does not invent lead-time matching beyond the 14-day historical evidence boundary', () => {
    const result = buildComparableResearchDefinition({
      expected_move: {
        straddle_pct: 0.06,
        timing: 'unknown',
        lead_time_days: 21,
      },
    });

    expect(result?.timing).toBe('all');
    expect(result?.currentLeadDays).toBe(21);
    expect(result?.minLead).toBeNull();
    expect(result?.maxLead).toBeNull();
    expect(result?.href).not.toContain('timing=');
    expect(result?.href).not.toContain('minLead=');
  });

  it('does not create comparable research without a valid event-specific implied move', () => {
    expect(buildComparableResearchDefinition(null)).toBeNull();
    expect(buildComparableResearchDefinition({ expected_move: null })).toBeNull();
    expect(
      buildComparableResearchDefinition({ expected_move: { straddle_pct: 0 } }),
    ).toBeNull();
  });
});
