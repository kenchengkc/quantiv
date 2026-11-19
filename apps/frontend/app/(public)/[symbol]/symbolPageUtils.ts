import { finiteDisplayForecast } from '@/lib/displayForecast';
import type { SymbolDetail } from './symbolPageTypes';

/** A retained forecast ID can outlive the decision to publish that event's ML. */
export function historicalMlForecast(row: NonNullable<SymbolDetail['earnings_history']>[number]): number | null {
  const snapshot = row.ml_snapshot_date?.slice(0, 10);
  if (row.forecast_frozen_eligible !== true || row.ml_publication_status === 'held' ||
      row.display_forecast_method === 'ml_unavailable' || !snapshot || snapshot >= row.date.slice(0, 10)) {
    return null;
  }
  return finiteDisplayForecast(row.em_ml_pct);
}

export function parseLocalDate(iso: string): Date {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  return new Date(year, (month ?? 1) - 1, day ?? 1);
}

export function shortDate(iso: string | undefined | null) {
  if (!iso) return '—';
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

export function axisDate(iso: string | undefined | null) {
  if (!iso) return '—';
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}
