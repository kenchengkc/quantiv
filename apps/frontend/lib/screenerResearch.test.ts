import { describe, expect, it } from 'vitest';
import {
  applyScreenerResearchQuery,
  canonicalScreenerQuery,
  parseScreenerResearchQuery,
  type ResearchScreenerEvent,
} from './screenerResearch';

const rows: ResearchScreenerEvent[] = [
  {
    ticker: 'AAPL',
    earnings_date: '2026-10-29',
    timing: 'after_market_close',
    spot_price: 200,
    em_method: 'ml_lightgbm',
    em_straddle_pct: 0.08,
    em_ml_pct: 0.06,
    hist_move_avg_4q: 0.05,
    iv_rank: 0.8,
    p10: 0.02,
    p90: 0.09,
  },
  {
    ticker: 'MSFT',
    earnings_date: '2026-10-28',
    timing: 'before_market_open',
    spot_price: 300,
    em_method: 'ml_lightgbm',
    em_straddle_pct: 0.11,
    em_ml_pct: 0.09,
    hist_move_avg_4q: 0.10,
    iv_rank: 0.2,
    p10: 0.02,
    p90: 0.12,
  },
  {
    ticker: 'LOWP',
    earnings_date: '2026-10-30',
    timing: 'after_market_close',
    spot_price: 8,
    em_method: 'options_math',
    em_straddle_pct: 0.15,
    hist_move_avg_4q: 0.05,
    iv_rank: 0.5,
  },
];

describe('screener research query', () => {
  it('normalizes URL state into one canonical contract', () => {
    const params = new URLSearchParams(
      'q=aap&sp500=1&minSpot=25&timing=amc&ml=1&preset=rich_vol&sort=edge&dir=asc',
    );
    const query = parseScreenerResearchQuery(params);

    expect(query).toEqual({
      q: 'AAP',
      sp500: true,
      minSpot: 25,
      timing: 'amc',
      mlOnly: true,
      preset: 'rich_vol',
      sort: 'edge',
      dir: 'asc',
    });
    expect(canonicalScreenerQuery(query)).toEqual({
      q: 'AAP',
      sp500: true,
      min_spot: 25,
      timing: 'amc',
      ml_only: true,
      preset: 'rich_vol',
      sort: 'edge',
      direction: 'asc',
    });
  });

  it('matches the screener rich-vol and ML filter semantics', () => {
    const query = parseScreenerResearchQuery(
      new URLSearchParams('ml=1&preset=rich_vol&sort=hist_edge&dir=desc'),
    );
    const result = applyScreenerResearchQuery(rows, query);

    expect(result.map((row) => row.ticker)).toEqual(['AAPL']);
  });

  it('applies the same default min-spot floor as the product', () => {
    const query = parseScreenerResearchQuery(new URLSearchParams('sort=date&dir=asc'));
    const result = applyScreenerResearchQuery(rows, query);

    expect(result.map((row) => row.ticker)).toEqual(['MSFT', 'AAPL']);
  });

  it('uses deterministic ticker tie-breaking', () => {
    const tied = [
      { ...rows[0], ticker: 'ZZZ', em_straddle_pct: 0.08, em_ml_pct: 0.06 },
      { ...rows[0], ticker: 'AAA', em_straddle_pct: 0.08, em_ml_pct: 0.06 },
    ];
    const query = parseScreenerResearchQuery(
      new URLSearchParams('sort=edge&dir=desc'),
    );

    expect(applyScreenerResearchQuery(tied, query).map((row) => row.ticker)).toEqual([
      'AAA',
      'ZZZ',
    ]);
  });
});
