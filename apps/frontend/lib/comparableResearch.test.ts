import { describe, expect, it } from 'vitest';
import { buildComparableResearchDefinition } from './comparableResearch';

describe('comparable research definition', () => {
  it('uses the current straddle move and AMC session transparently', () => {
    const result = buildComparableResearchDefinition({
      expected_move: {
        straddle_pct: 0.0982,
        timing: 'after_market_close',
      },
    });

    expect(result).not.toBeNull();
    expect(result).toMatchObject({
      currentImplied: 0.0982,
      minImplied: 0.07365,
      maxImplied: 0.12275,
      timing: 'amc',
    });
    expect(result?.href).toContain('/research?');
    expect(result?.href).toContain('timing=amc');
    expect(result?.href).toContain('sort=ratio');
    expect(result?.href).toContain('dir=desc');
  });

  it('falls back to the symbol-level timing when the expected-move object omits it', () => {
    const result = buildComparableResearchDefinition({
      expected_move: { straddle_pct: 0.04 },
      next_earnings_timing: 'before_market_open',
    });

    expect(result?.timing).toBe('bmo');
    expect(result?.href).toContain('timing=bmo');
    expect(result?.minImplied).toBe(0.03);
    expect(result?.maxImplied).toBe(0.05);
  });

  it('keeps unknown-session comparables explicit instead of inventing timing', () => {
    const result = buildComparableResearchDefinition({
      expected_move: { straddle_pct: 0.06, timing: 'unknown' },
    });

    expect(result?.timing).toBe('all');
    expect(result?.href).not.toContain('timing=');
  });

  it('does not create comparable research without a valid event-specific implied move', () => {
    expect(buildComparableResearchDefinition(null)).toBeNull();
    expect(buildComparableResearchDefinition({ expected_move: null })).toBeNull();
    expect(
      buildComparableResearchDefinition({ expected_move: { straddle_pct: 0 } }),
    ).toBeNull();
  });
});
