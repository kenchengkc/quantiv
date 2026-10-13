// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { BackendProxyError } from '@/lib/backendProxy';

const mocks = vi.hoisted(() => ({
  proxy: vi.fn(), configured: vi.fn(), nightly: vi.fn(), rateLimit: vi.fn(),
  policy: vi.fn(),
}));
vi.mock('@/lib/backendProxy', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/backendProxy')>(),
  proxyJsonPost: mocks.proxy,
  backendProxyConfigured: mocks.configured,
}));
vi.mock('@/lib/rateLimit', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/rateLimit')>(),
  enforceRateLimit: mocks.rateLimit,
}));
vi.mock('../_shared', async (importOriginal) => ({
  ...await importOriginal<typeof import('../_shared')>(),
  loadSymbolJson: mocks.nightly,
  loadPredictionPublicationPolicy: mocks.policy,
}));

import { POST } from './route';

const healthy = { symbol: 'HEALTHY', horizon_days: 7, earnings_date: '2099-11-13' };
const held = { symbol: 'HELD', horizon_days: 7, earnings_date: '2099-11-13' };

function post(items: unknown[]) {
  return POST(new NextRequest('http://localhost/api/ml/batch-predict', {
    method: 'POST', body: JSON.stringify({ items, allow_partial: true }),
  }));
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.configured.mockReturnValue(true);
  mocks.rateLimit.mockResolvedValue(null);
  mocks.nightly.mockImplementation((symbol: string) => ({ expected_move: {
    ml_publication_status: symbol === 'HELD' ? 'held' : 'eligible',
    ml_publication_reason: symbol === 'HELD' ? 'model_critical' : null,
    earnings_date: '2099-11-13', model_horizon: 7, em_ml_pct: .9,
    ml_snapshot_date: '2099-11-12', forecast_scored_at: '2099-11-12T12:00:00Z',
    forecast_model_bundle_id: 'a'.repeat(64),
  } }));
  mocks.policy.mockReturnValue(false);
  mocks.proxy.mockImplementation(async (_path: string, body: { items: typeof healthy[] }) => ({
    items: body.items.map((item) => ({ ok: true, ...item, response: {
      ...item, em_ml_pct: .05, source: 'cached', feature_snapshot_date: '2099-11-12',
      forecast_scored_at: '2099-11-12T12:00:00Z', model_bundle_id: 'a'.repeat(64),
    } })),
    served_at: '2099-11-01T12:00:00Z',
  }));
});

describe('public batch prediction publication guard', () => {
  it('publishes healthy items and withholds held or unlisted horizons in input order', async () => {
    const response = await post([healthy, held, { ...healthy, horizon_days: 14 }]);
    expect(response.status).toBe(200);
    expect((await response.json()).items).toMatchObject([
      { ok: true, symbol: 'HEALTHY', response: { em_ml_pct: .05 } },
      { ok: false, symbol: 'HELD', publication_status: 'held', publication_reason: 'model_critical' },
      { ok: false, symbol: 'HEALTHY', horizon_days: 14, publication_status: 'held' },
    ]);
    expect(mocks.proxy).toHaveBeenCalledWith('/api/ml/batch-predict', {
      items: [{ ...healthy, intended_use: 'end_of_day_research' }], allow_partial: true,
    }, { timeoutMs: 10_000 });
  });

  it('withholds an entirely held batch without consulting backend caches', async () => {
    const response = await post([held]);
    expect(response.status).toBe(200);
    expect((await response.json()).items).toMatchObject([
      { ok: false, symbol: 'HELD', publication_status: 'held' },
    ]);
    expect(mocks.proxy).not.toHaveBeenCalled();
  });

  it.each(['not_configured', 'network_error'])(
    'keeps held items unavailable during %s while returning healthy nightly fallback',
    async (failure) => {
      if (failure === 'not_configured') mocks.configured.mockReturnValue(false);
      else mocks.proxy.mockRejectedValue(new Error('backend unavailable'));
      const response = await post([healthy, held]);
      expect((await response.json()).items).toMatchObject([
        { ok: true, symbol: 'HEALTHY', response: { source: 'nightly_fallback', em_ml_pct: .9 } },
        { ok: false, symbol: 'HELD', publication_status: 'held' },
      ]);
    },
  );

  it('binds missing dates and preserves legacy historical items in the same batch', async () => {
    mocks.nightly.mockImplementation((symbol: string) => symbol === 'LEGACY'
      ? { expected_move: { em_ml_pct: .06 } }
      : { expected_move: { ml_publication_status: 'eligible',
        earnings_date: '2099-11-13', model_horizon: 7 } });
    await post([{ symbol: 'HEALTHY', horizon_days: 7 }, {
      symbol: 'LEGACY', horizon_days: 14, earnings_date: '2020-01-03',
    }]);
    expect(mocks.proxy).toHaveBeenCalledWith('/api/ml/batch-predict', {
      items: [{ ...healthy, intended_use: 'end_of_day_research' }, {
        symbol: 'LEGACY', horizon_days: 14, earnings_date: '2020-01-03',
        intended_use: 'end_of_day_research',
      }], allow_partial: true,
    }, { timeoutMs: 10_000 });
  });

  it('uses approved per-item nightly values for an old response without affecting matching items', async () => {
    mocks.proxy.mockResolvedValue({ items: [
      { ok: true, ...healthy, response: { ...healthy, em_ml_pct: .05, source: 'computed',
        feature_snapshot_date: '2099-11-12', forecast_scored_at: '2099-11-12T12:00:00Z',
        model_bundle_id: 'a'.repeat(64) } },
      { ok: true, ...healthy, symbol: 'STALE', response: {
        ...healthy, symbol: 'STALE', em_ml_pct: .8, source: 'cached',
        feature_snapshot_date: '2099-11-11', forecast_scored_at: '2099-11-11T12:00:00Z',
        model_bundle_id: 'a'.repeat(64),
      } },
    ], served_at: '2099-11-12T13:00:00Z' });
    const response = await post([healthy, held, { ...healthy, symbol: 'STALE' }]);
    expect((await response.json()).items).toMatchObject([
      { ok: true, symbol: 'HEALTHY', response: { source: 'computed', em_ml_pct: .05 } },
      { ok: false, symbol: 'HELD', publication_status: 'held' },
      { ok: true, symbol: 'STALE', response: { source: 'nightly_fallback', em_ml_pct: .9,
        fallback_reason: 'backend_prediction_not_current' } },
    ]);
  });

  it.each([{ items: [] }, { items: [{ ok: true }] }, {
    items: [{ ok: true }, { ok: true }, { ok: true }],
  }])(
    'rejects malformed batch result counts and returns only approved fallback values: %j',
    async ({ items }) => {
      mocks.proxy.mockResolvedValue({ items, served_at: '2099-11-12T13:00:00Z' });
      const response = await post([healthy, { ...healthy, symbol: 'SECOND' }]);
      expect((await response.json()).items).toMatchObject([
        { ok: true, symbol: 'HEALTHY', response: { source: 'nightly_fallback', em_ml_pct: .9,
          fallback_reason: 'backend_batch_response_invalid' } },
        { ok: true, symbol: 'SECOND', response: { source: 'nightly_fallback', em_ml_pct: .9,
          fallback_reason: 'backend_batch_response_invalid' } },
      ]);
    },
  );

  it('withholds unlisted future symbols while preserving explicit historical batch items', async () => {
    mocks.policy.mockReturnValue(true);
    mocks.nightly.mockReturnValue(null);
    const response = await post([{ ...healthy, symbol: 'UNKNOWN' }, {
      ...healthy, symbol: 'HISTORY', earnings_date: '2020-01-03',
    }]);
    expect((await response.json()).items).toMatchObject([
      { ok: false, symbol: 'UNKNOWN', publication_status: 'held' },
      { ok: true, symbol: 'HISTORY' },
    ]);
  });

  it('keeps approved fallback and held items when the backend rejects the whole batch', async () => {
    mocks.proxy.mockRejectedValue(new BackendProxyError('No fresh feature snapshot', 404));
    const response = await post([healthy, held]);
    expect(response.status).toBe(200);
    expect((await response.json()).items).toMatchObject([
      { ok: true, symbol: 'HEALTHY', response: { source: 'nightly_fallback', em_ml_pct: .9,
        fallback_reason: 'backend_prediction_unavailable' } },
      { ok: false, symbol: 'HELD', publication_status: 'held' },
    ]);
    expect(mocks.proxy).toHaveBeenCalledWith('/api/ml/batch-predict', {
      items: [{ ...healthy, intended_use: 'end_of_day_research' }], allow_partial: true,
    }, { timeoutMs: 10_000 });
  });

  it('preserves exact legacy backend errors alongside an approved current item', async () => {
    mocks.nightly.mockImplementation((symbol: string) => symbol === 'LEGACY' ? null : {
      expected_move: { ml_publication_status: 'eligible', model_horizon: 7,
        earnings_date: '2099-11-13', em_ml_pct: .9 },
    });
    mocks.proxy.mockRejectedValue(new BackendProxyError('No fresh feature snapshot', 404));
    const response = await post([healthy, { ...healthy, symbol: 'LEGACY' }]);
    expect(response.status).toBe(200);
    expect((await response.json()).items).toMatchObject([
      { ok: true, symbol: 'HEALTHY', response: { source: 'nightly_fallback', em_ml_pct: .9 } },
      { ok: false, symbol: 'LEGACY', error_status: 404, error: 'No fresh feature snapshot' },
    ]);
  });

  it('preserves whole-batch legacy HTTP errors', async () => {
    mocks.nightly.mockReturnValue(null);
    mocks.proxy.mockRejectedValue(new BackendProxyError('No fresh feature snapshot', 404));
    expect((await post([healthy])).status).toBe(404);
  });
});
