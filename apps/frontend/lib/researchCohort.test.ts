import { describe, expect, it } from 'vitest';
import {
  applyCohortQuery,
  parseCohortQuery,
  summarizeCohort,
  type CohortEvent,
} from './researchCohort';

const rows: CohortEvent[] = [
  {
    ticker: 'AAPL',
    date: '2026-07-30',
    timing: 'after_market_close',
    fiscal_q: 'Q2',
    actual: -0.0735,
    realized_abs: 0.0735,
    implied: 0.0506,
    implied_as_of: '2026-07-30',
    implied_expiration: '2026-08-12',
    implied_dte: 13,
    implied_lead_days: 0,
    implied_atm_strike: 332.5,
    implied_atm_iv: 0.3334,
    implied_quality_status: 'decision_eligible_eod',
    eps_surprise_pct: -0.0089,
    rev_surprise_pct: -0.0127,
    edge: 0.0229,
    ratio: 0.0735 / 0.0506,
    outside_implied: true,
  },
  {
    ticker: 'MSFT',
    date: '2026-07-29',
    timing: 'after_market_close',
    fiscal_q: 'Q4',
    actual: 0.03,
    realized_abs: 0.03,
    implied: 0.05,
    implied_as_of: '2026-07-29',
    implied_expiration: '2026-08-07',
    implied_dte: 9,
    implied_lead_days: 0,
    implied_atm_strike: 500,
    implied_atm_iv: 0.27,
    implied_quality_status: 'decision_eligible_eod',
    eps_surprise_pct: 0.05,
    rev_surprise_pct: 0.02,
    edge: -0.02,
    ratio: 0.6,
    outside_implied: false,
  },
  {
    ticker: 'WMT',
    date: '2026-05-21',
    timing: 'before_market_open',
    fiscal_q: 'Q1',
    actual: 0.08,
    realized_abs: 0.08,
    implied: 0.06,
    implied_as_of: '2026-05-20',
    implied_expiration: '2026-05-22',
    implied_dte: 2,
    implied_lead_days: 1,
    implied_atm_strike: 100,
    implied_atm_iv: 0.4,
    implied_quality_status: 'decision_eligible_eod',
    eps_surprise_pct: 0.03,
    rev_surprise_pct: null,
    edge: 0.02,
    ratio: 4 / 3,
    outside_implied: true,
  },
];

describe('historical cohort query', () => {
  it('normalizes filters and bounds limit', () => {
    const query = parseCohortQuery(
      new URLSearchParams('q=aapl&timing=amc&quarter=Q2&outcome=outside&eps=miss&minImplied=.04&limit=9000'),
    );
    expect(query).toMatchObject({
      q: 'AAPL',
      timing: 'amc',
      quarter: 'Q2',
      outcome: 'outside',
      eps: 'miss',
      minImplied: 0.04,
      limit: 1000,
    });
  });

  it('filters on point-in-time research attributes', () => {
    const query = parseCohortQuery(
      new URLSearchParams('timing=amc&outcome=outside&eps=miss&sort=ratio&dir=desc'),
    );
    const result = applyCohortQuery(rows, query);
    expect(result.map((row) => row.ticker)).toEqual(['AAPL']);
  });

  it('sorts with deterministic ticker tie-breaking', () => {
    const tied = [
      { ...rows[1], ticker: 'ZZZ', implied: 0.05 },
      { ...rows[1], ticker: 'AAA', implied: 0.05 },
    ];
    const query = parseCohortQuery(new URLSearchParams('sort=implied&dir=desc'));
    expect(applyCohortQuery(tied, query).map((row) => row.ticker)).toEqual(['AAA', 'ZZZ']);
  });

  it('summarizes implied-vs-realized calibration', () => {
    const summary = summarizeCohort(rows);
    expect(summary.events).toBe(3);
    expect(summary.symbols).toBe(3);
    expect(summary.outsideRate).toBeCloseTo(2 / 3);
    expect(summary.avgImplied).toBeCloseTo((0.0506 + 0.05 + 0.06) / 3);
    expect(summary.avgRealized).toBeCloseTo((0.0735 + 0.03 + 0.08) / 3);
    expect(summary.meanAbsoluteError).toBeCloseTo((0.0229 + 0.02 + 0.02) / 3);
  });
});
