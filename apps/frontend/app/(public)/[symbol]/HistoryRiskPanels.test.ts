import { describe, expect, it } from 'vitest';
import {
  buildHistorySeries,
  eventStudyEvidenceCounts,
  historyRowsToCsv,
  type HistoryPoint,
} from './HistoryRiskPanels';

describe('buildHistorySeries', () => {
  it('keeps up to twelve chronological events for the local event study', () => {
    const rows = Array.from({ length: 13 }, (_, index) => ({
      date: `202${Math.floor(index / 4) + 3}-${String((index % 4) * 3 + 1).padStart(2, '0')}-15`,
      timing: 'after_market_close',
      q: `Q${(index % 4) + 1}`,
      actual: index / 100,
    }));

    const result = buildHistorySeries(rows);

    expect(result).toHaveLength(12);
    expect(result[0].actual).toBe(0.01);
    expect(result.at(-1)?.actual).toBe(0.12);
  });
});

describe('historyRowsToCsv', () => {
  it('exports the exact cohort with explicit units and nullable evidence', () => {
    const point: HistoryPoint = {
      q: 'Q2, FY26',
      date: '2026-06-11',
      timing: 'after_market_close',
      implied: 0.08,
      actual: -0.06755,
      epsActual: 5.96,
      epsEstimate: 5.9385,
      epsSurprise: 0.00362,
      revActual: 6618000000,
      revEstimate: 6584794456,
      revSurprise: 0.005043,
    };

    const csv = historyRowsToCsv('ADBE', [point]);
    const [header, row] = csv.split('\n');

    expect(header).toContain('realized_move_pct');
    expect(header).toContain('exceeded_implied');
    expect(row).toContain('ADBE,"Q2, FY26",2026-06-11,after_market_close');
    expect(row).toContain('-6.755000,6.755000,down,8.000000,false');
    expect(row).toContain('5.96,5.9385,0.362000');
  });
});

describe('eventStudyEvidenceCounts', () => {
  it('does not count missing EPS or implied evidence as a beat', () => {
    const base = {
      q: 'Q1',
      date: '2026-01-01',
      timing: 'after_market_close',
      actual: 0.05,
      epsActual: null,
      epsEstimate: null,
      revActual: null,
      revEstimate: null,
      revSurprise: null,
    };
    const counts = eventStudyEvidenceCounts([
      { ...base, implied: null, epsSurprise: null },
      { ...base, q: 'Q2', implied: 0.04, epsSurprise: 0.02 },
      { ...base, q: 'Q3', implied: 0.08, epsSurprise: -0.01 },
    ]);

    expect(counts).toEqual({
      impliedObservations: 2,
      impliedExceedances: 1,
      epsObservations: 2,
      epsBeats: 1,
    });
  });
});
