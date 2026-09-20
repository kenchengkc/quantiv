import { describe, expect, it } from 'vitest';
import {
  buildHistorySeries,
  eventStudyEvidenceCounts,
  historyRowsToCsv,
  medianAbsoluteHistoryMove,
  priorHistoricalForecastWindow,
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

  it('uses fiscal quarter metadata before falling back to the report-date quarter', () => {
    const result = buildHistorySeries([
      {
        date: '2026-02-05',
        timing: 'after_market_close',
        fiscal_year: 2025,
        fiscal_q: 'Q4',
        actual: 0.03,
      },
    ]);

    expect(result[0]?.q).toBe('Q4 25');
  });

  it('carries the final frozen ML forecast into the event study', () => {
    const result = buildHistorySeries([
      {
        date: '2026-09-16',
        timing: 'after_market_close',
        q: 'Q4 26',
        actual: -0.071,
        em_ml_pct: 0.083,
        ml_snapshot_date: '2026-09-15',
        forecast_frozen_eligible: true,
        model_horizon: 1,
        p10: 0.03,
        p50: 0.08,
        p90: 0.14,
      },
    ]);

    expect(result[0]?.model).toBe(0.083);
    expect(result[0]?.modelAsOf).toBe('2026-09-15');
    expect(result[0]?.modelHorizon).toBe(1);
    expect(result[0]?.modelP50).toBe(0.08);
  });


  it('suppresses unaudited historical ML from the event study', () => {
    const result = buildHistorySeries([
      {
        date: '2026-09-16',
        timing: 'after_market_close',
        q: 'Q4 26',
        actual: -0.071,
        em_ml_pct: 0.083,
        ml_snapshot_date: '2026-09-15',
        model_horizon: 1,
      },
    ]);

    expect(result[0]?.model).toBeNull();
    expect(result[0]?.modelAsOf).toBe('2026-09-15');
  });

});

describe('priorHistoricalForecastWindow', () => {
  it('uses only the four realized events before the target earnings date', () => {
    const rows = buildHistorySeries([
      { date: '2026-09-16', timing: 'after_market_close', actual: 0.013788 },
      { date: '2026-06-23', timing: 'after_market_close', actual: -0.001292 },
      { date: '2026-03-19', timing: 'after_market_close', actual: 0.007694 },
      { date: '2025-12-18', timing: 'after_market_close', actual: 0.005782 },
      { date: '2025-09-18', timing: 'after_market_close', actual: 0.023179 },
      { date: '2025-06-24', timing: 'after_market_close', actual: -0.032722 },
    ]);

    const cohort = priorHistoricalForecastWindow(rows, '2026-09-16');

    expect(cohort.map((row) => row.date)).toEqual([
      '2025-09-18',
      '2025-12-18',
      '2026-03-19',
      '2026-06-23',
    ]);
    expect(medianAbsoluteHistoryMove(cohort)).toBeCloseTo(0.006738, 6);
  });
});

describe('historyRowsToCsv', () => {
  it('exports the exact cohort with explicit units and nullable evidence', () => {
    const point: HistoryPoint = {
      q: 'Q2, FY26',
      date: '2026-06-11',
      timing: 'after_market_close',
      implied: 0.08,
      impliedAsOf: '2026-06-11',
      impliedExpiration: '2026-06-19',
      impliedDte: 8,
      impliedLeadDays: 0,
      impliedAtmStrike: 410,
      impliedStraddleAbs: 32.8,
      impliedAtmIv: 0.42,
      model: 0.075,
      modelAsOf: '2026-06-10',
      modelHorizon: 1,
      modelP10: 0.03,
      modelP25: 0.05,
      modelP50: 0.075,
      modelP75: 0.10,
      modelP90: 0.13,
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
    expect(header).toContain('ml_forecast_pct');
    expect(row).toContain('ADBE,"Q2, FY26",2026-06-11,after_market_close');
    expect(row).toContain(
      '-6.755000,6.755000,down,8.000000,2026-06-11,2026-06-19,8,0,410,32.8,42.000000,false',
    );
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
      impliedAsOf: null,
      impliedExpiration: null,
      impliedDte: null,
      impliedLeadDays: null,
      impliedAtmStrike: null,
      impliedStraddleAbs: null,
      impliedAtmIv: null,
      modelAsOf: null,
      modelHorizon: null,
      modelP10: null,
      modelP25: null,
      modelP50: null,
      modelP75: null,
      modelP90: null,
      epsActual: null,
      epsEstimate: null,
      revActual: null,
      revEstimate: null,
      revSurprise: null,
    };
    const counts = eventStudyEvidenceCounts([
      { ...base, implied: null, model: null, epsSurprise: null },
      { ...base, q: 'Q2', implied: 0.04, model: 0.04, epsSurprise: 0.02 },
      { ...base, q: 'Q3', implied: 0.08, model: 0.06, epsSurprise: -0.01 },
    ]);

    expect(counts).toEqual({
      impliedObservations: 2,
      impliedExceedances: 1,
      modelObservations: 2,
      modelExceedances: 1,
      epsObservations: 2,
      epsBeats: 1,
    });
  });
});
