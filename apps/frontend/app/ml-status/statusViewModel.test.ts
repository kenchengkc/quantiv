import { describe, expect, it } from 'vitest';
import {
  classifyStatus,
  freshCoverageRatio,
  importRowDelta,
  sortedImportHorizons,
  sortedModelRows,
  type MlStatusResponse,
} from './statusViewModel';

const baseStatus: MlStatusResponse = {
  ok: true,
  status: 'ok',
  checked_at: '2026-05-25T12:00:00Z',
  fresh_window_days: 7,
  max_snapshot_age_days: 7,
  models_dir: '/data/models',
  available_model_horizons: [7, 14],
  loaded_model_horizons: [7],
  redis_available: true,
  postgres_available: true,
  data: {
    total_feature_rows: 414,
    fresh_feature_rows: 168,
    fresh_distinct_symbols: 82,
    fresh_distinct_events: 94,
    latest_snapshot_date: '2026-05-24',
    latest_scored_at: '2026-05-24T01:00:00Z',
  },
  rows_by_horizon: [],
  latest_import: {
    parquet_file: 'forecasts_2026-05-24.parquet',
    imported_at: '2026-05-24T01:10:00Z',
    import_mode: 'full',
    source_rows: 424,
    selected_rows: 424,
    duplicate_rows: 20,
    duplicate_keys: 10,
    rows_upserted: 414,
    feature_vector_rows: 414,
    distinct_symbols: 180,
    distinct_events: 190,
    min_snapshot_date: '2026-05-12',
    max_snapshot_date: '2026-05-24',
    horizons: { '21': 174, '7': 100, '14': 140 },
  },
  models: [
    {
      horizon_days: 14,
      point_model_exists: true,
      quantile_model_count: 5,
      feature_count: 48,
      feature_schema_hash: 'def',
      model_version: 'v3',
      trained_at: null,
      val_mae: 0.03,
      loaded: false,
      loaded_at: null,
      model_mtime: null,
      metadata_mtime: null,
    },
    {
      horizon_days: 7,
      point_model_exists: true,
      quantile_model_count: 5,
      feature_count: 48,
      feature_schema_hash: 'abc',
      model_version: 'v3',
      trained_at: null,
      val_mae: 0.04,
      loaded: true,
      loaded_at: null,
      model_mtime: null,
      metadata_mtime: null,
    },
  ],
};

describe('ML status view model', () => {
  it('classifies status from backend health signals', () => {
    expect(classifyStatus(baseStatus)).toBe('ok');
    expect(classifyStatus({ ...baseStatus, postgres_available: false })).toBe('degraded');
    expect(classifyStatus(null)).toBe('offline');
  });

  it('computes fresh feature coverage and import row delta', () => {
    expect(freshCoverageRatio(baseStatus)).toBeCloseTo(168 / 414);
    expect(importRowDelta(baseStatus.latest_import)).toBe(10);
  });

  it('sorts model and import horizons numerically', () => {
    expect(sortedModelRows(baseStatus.models).map((row) => row.horizon_days)).toEqual([7, 14]);
    expect(sortedImportHorizons(baseStatus.latest_import)).toEqual([
      ['7', 100],
      ['14', 140],
      ['21', 174],
    ]);
  });
});
