// @vitest-environment node
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadPredictionPublicationPolicy } from './_shared';
import { guardPublishedPrediction, matchesPublishedPrediction } from './_publicationGuard';

const request = { symbol: 'A', horizon_days: 7 as const,
  earnings_date: '2099-11-13', intended_use: 'end_of_day_research' as const };
const nightly = { expected_move: {
  ml_publication_status: 'eligible' as const, model_horizon: 7,
  earnings_date: '2099-11-13', ml_snapshot_date: '2099-11-12',
  forecast_scored_at: '2099-11-12T12:00:00.123456Z', forecast_model_bundle_id: 'a'.repeat(64),
} };
const response = { symbol: 'A', horizon_days: 7, earnings_date: '2099-11-13',
  feature_snapshot_date: '2099-11-12', model_bundle_id: 'a'.repeat(64), source: 'computed',
  forecast_scored_at: '2099-11-12T12:00:00.123456Z' };

describe('published source identity', () => {
  it('accepts equivalent timezone/precision spelling of the exact scoring clock', () => {
    expect(matchesPublishedPrediction(request, nightly, { ...response,
      forecast_scored_at: '2099-11-12T07:00:00.123456000-05:00' })).toBe(true);
  });

  it('rejects a scoring clock that differs by one microsecond', () => {
    expect(matchesPublishedPrediction(request, nightly, { ...response,
      forecast_scored_at: '2099-11-12T12:00:00.123457Z' })).toBe(false);
  });

  it('holds missing current-day policy entries while preserving explicit past dates', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-13T16:00:00Z'));
    expect(guardPublishedPrediction({ ...request, earnings_date: '2026-10-13' }, null, true))
      .toMatchObject({ holdReason: 'event_not_eligible' });
    expect(guardPublishedPrediction({ ...request, earnings_date: '2026-10-12' }, null, true))
      .toMatchObject({ holdReason: null, publicationBound: false });
    vi.useRealTimers();
  });
});

describe('global publication policy loader', () => {
  let directory: string;
  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), 'quantiv-public-policy-'));
    mkdirSync(join(directory, 'public'));
    vi.spyOn(process, 'cwd').mockReturnValue(directory);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    rmSync(directory, { recursive: true, force: true });
  });

  it('uses the emitted policy marker from the generated control snapshot', () => {
    writeFileSync(join(directory, 'public/control-plane.json'), JSON.stringify({
      model: { publication_policy: 'quantiv.event-forecast-eligibility.v1' },
    }));
    expect(loadPredictionPublicationPolicy()).toBe(true);
  });

  it('preserves old snapshots and missing pre-policy control files', () => {
    expect(loadPredictionPublicationPolicy()).toBe(false);
    writeFileSync(join(directory, 'public/control-plane.json'), JSON.stringify({ model: {} }));
    expect(loadPredictionPublicationPolicy()).toBe(false);
  });
});
