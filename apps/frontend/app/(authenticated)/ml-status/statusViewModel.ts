export type MlStatusDataRow = {
  total_feature_rows: number;
  fresh_feature_rows: number;
  fresh_distinct_symbols: number;
  fresh_distinct_events: number;
  latest_snapshot_date: string | null;
  latest_scored_at: string | null;
};

export type MlStatusHorizonRow = {
  horizon_days: number;
  total_feature_rows: number;
  fresh_feature_rows: number;
  latest_snapshot_date: string | null;
  latest_scored_at: string | null;
};

export type MlStatusCoverageGapRow = {
  horizon_days: number;
  model_available: boolean;
  has_any_feature_rows: boolean;
  has_fresh_feature_rows: boolean;
  total_feature_rows: number;
  fresh_feature_rows: number;
  unavailable_reason: string | null;
};

export type MlStatusImportRow = {
  parquet_file: string;
  imported_at: string;
  import_mode: string;
  source_rows: number;
  selected_rows: number;
  duplicate_rows: number;
  duplicate_keys: number;
  rows_upserted: number;
  feature_vector_rows: number;
  distinct_symbols: number;
  distinct_events: number;
  min_snapshot_date: string | null;
  max_snapshot_date: string | null;
  horizons: Record<string, number>;
};

export type MlStatusModelRow = {
  horizon_days: number;
  point_model_exists: boolean;
  quantile_model_count: number;
  feature_count: number | null;
  feature_schema_hash: string | null;
  model_version: string | null;
  trained_at: string | null;
  val_mae: number | null;
  loaded: boolean;
  loaded_at: string | null;
  model_mtime: string | null;
  metadata_mtime: string | null;
};

export type MlStatusResponse = {
  ok: boolean;
  status: string;
  checked_at: string;
  fresh_window_days: number;
  max_snapshot_age_days: number;
  models_dir: string;
  supported_horizons?: number[];
  available_model_horizons: number[];
  loaded_model_horizons: number[];
  missing_model_horizons?: number[];
  missing_fresh_horizons?: number[];
  redis_available: boolean;
  postgres_available: boolean;
  data: MlStatusDataRow | null;
  rows_by_horizon: MlStatusHorizonRow[];
  coverage_gaps?: MlStatusCoverageGapRow[];
  latest_import: MlStatusImportRow | null;
  models: MlStatusModelRow[];
};

export type StatusKind = 'ok' | 'degraded' | 'offline';

export function compactNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value);
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  });
}

export function freshCoverageRatio(status: MlStatusResponse | null): number | null {
  const data = status?.data;
  if (!data || data.total_feature_rows <= 0) return null;
  return data.fresh_feature_rows / data.total_feature_rows;
}

export function classifyStatus(status: MlStatusResponse | null): StatusKind {
  if (!status) return 'offline';
  if (status.ok && status.postgres_available && status.available_model_horizons.length > 0) {
    return 'ok';
  }
  return 'degraded';
}

export function importRowDelta(latestImport: MlStatusImportRow | null): number | null {
  if (!latestImport) return null;
  return latestImport.source_rows - latestImport.rows_upserted;
}

export function sortedHorizonRows(rows: MlStatusHorizonRow[]): MlStatusHorizonRow[] {
  return [...rows].sort((a, b) => a.horizon_days - b.horizon_days);
}

export function sortedCoverageGaps(rows: MlStatusCoverageGapRow[] = []): MlStatusCoverageGapRow[] {
  return [...rows].sort((a, b) => a.horizon_days - b.horizon_days);
}

export function sortedModelRows(rows: MlStatusModelRow[]): MlStatusModelRow[] {
  return [...rows].sort((a, b) => a.horizon_days - b.horizon_days);
}

export function sortedImportHorizons(latestImport: MlStatusImportRow | null): [string, number][] {
  if (!latestImport) return [];
  return Object.entries(latestImport.horizons)
    .filter(([, count]) => Number.isFinite(count))
    .sort(([a], [b]) => Number(a) - Number(b));
}
