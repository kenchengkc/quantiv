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

const request = { symbol: 'HEALTHY', horizon_days: 7, earnings_date: '2099-11-13' };
const nightly = {
  expected_move: {
    ml_publication_status: 'eligible', earnings_date: '2099-11-13',
    model_horizon: 7, em_ml_pct: .05,
    ml_snapshot_date: '2099-11-12', forecast_scored_at: '2099-11-12T12:00:00Z',
    forecast_model_bundle_id: 'a'.repeat(64),
  },
};

function post(body: unknown) {
  return POST(new NextRequest('http://localhost/api/ml/predict', {
    method: 'POST', body: JSON.stringify(body),
  }));
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.configured.mockReturnValue(true);
  mocks.rateLimit.mockResolvedValue(null);
  mocks.nightly.mockReturnValue(nightly);
  mocks.policy.mockReturnValue(false);
  mocks.proxy.mockResolvedValue({ ...request, source: 'cached', em_ml_pct: .05,
    feature_snapshot_date: '2099-11-12', forecast_scored_at: '2099-11-12T12:00:00+00:00',
    model_bundle_id: 'a'.repeat(64),
  });
});

describe('public prediction publication guard', () => {
  it('holds an explicitly rejected event before old backend inference', async () => {
    mocks.nightly.mockReturnValue({ expected_move: {
      ...nightly.expected_move, ml_publication_status: 'held',
      ml_publication_reason: 'model_critical', em_ml_pct: .9,
    } });
    const response = await post(request);
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({
      publication_status: 'held', publication_reason: 'model_critical',
    });
    expect(mocks.proxy).not.toHaveBeenCalled();
  });

  it.each([
    { ...request, earnings_date: '2099-12-01' },
    { ...request, horizon_days: 14 },
  ])('holds a request outside the approved event and horizon: %j', async (body) => {
    const response = await post(body);
    expect(response.status).toBe(404);
    expect(mocks.proxy).not.toHaveBeenCalled();
  });

  it('holds an explicit eligibility marker without its approved horizon', async () => {
    mocks.nightly.mockReturnValue({ expected_move: {
      ml_publication_status: 'eligible', earnings_date: '2099-11-13', em_ml_pct: .05,
    } });
    expect((await post(request)).status).toBe(404);
    expect(mocks.proxy).not.toHaveBeenCalled();
  });

  it('proxies the approved event and horizon', async () => {
    const response = await post(request);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ symbol: 'HEALTHY', em_ml_pct: .05 });
    expect(mocks.proxy).toHaveBeenCalledWith('/api/ml/predict', {
      ...request, intended_use: 'end_of_day_research',
    }, { timeoutMs: 5_000 });
  });

  it('binds an omitted event date before the backend auto-selects an older event', async () => {
    await post({ symbol: 'HEALTHY', horizon_days: 7 });
    expect(mocks.proxy).toHaveBeenCalledWith('/api/ml/predict', {
      ...request, intended_use: 'end_of_day_research',
    }, { timeoutMs: 5_000 });
  });

  it('preserves historical requests from payloads without the new publication marker', async () => {
    mocks.nightly.mockReturnValue({ expected_move: { em_ml_pct: .07 } });
    const body = { ...request, earnings_date: '2020-01-03' };
    expect((await post(body)).status).toBe(200);
    expect(mocks.proxy).toHaveBeenCalledWith('/api/ml/predict', {
      ...body, intended_use: 'end_of_day_research',
    }, { timeoutMs: 5_000 });
  });

  it('does not restore held static ML when the backend is unavailable', async () => {
    mocks.configured.mockReturnValue(false);
    mocks.nightly.mockReturnValue({ expected_move: {
      ...nightly.expected_move, ml_publication_status: 'held', em_ml_pct: .9,
    } });
    expect((await post(request)).status).toBe(404);
    expect(mocks.proxy).not.toHaveBeenCalled();
  });

  it.each([
    { feature_snapshot_date: '2099-11-11' },
    { forecast_scored_at: '2099-11-12T11:00:00Z' },
    { forecast_scored_at: undefined },
    { model_bundle_id: 'b'.repeat(64) },
    { model_bundle_id: undefined },
    { symbol: 'OTHER' },
    { earnings_date: '2099-12-01' },
    { horizon_days: 14 },
    { source: 'nightly_fallback' },
  ])('uses approved nightly ML when backend identity differs: %j', async (mutation) => {
    const baseline = await mocks.proxy();
    mocks.proxy.mockResolvedValue({ ...baseline, ...mutation, em_ml_pct: .9 });
    const response = await post(request);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      source: 'nightly_fallback', fallback_kind: 'static_ml', em_ml_pct: .05,
      feature_snapshot_date: '2099-11-12',
      fallback_reason: 'backend_prediction_not_current',
    });
  });

  it.each([{ symbol: 'UNKNOWN', horizon_days: 7 }, {
    symbol: 'UNKNOWN', horizon_days: 7, earnings_date: '2099-11-13',
  }])('holds unlisted events when global publication policy is active: %j', async (body) => {
    mocks.policy.mockReturnValue(true);
    mocks.nightly.mockReturnValue(null);
    expect((await post(body)).status).toBe(404);
    expect(mocks.proxy).not.toHaveBeenCalled();
  });

  it('preserves explicit past historical inference under the new global policy', async () => {
    mocks.policy.mockReturnValue(true);
    mocks.nightly.mockReturnValue(null);
    expect((await post({ ...request, earnings_date: '2020-01-03' })).status).toBe(200);
  });

  it.each([400, 404, 422])('serves the approved nightly subset after backend %s rejection', async (status) => {
    mocks.proxy.mockRejectedValue(new BackendProxyError('No fresh feature snapshot', status));
    const response = await post(request);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      source: 'nightly_fallback', em_ml_pct: .05,
      fallback_reason: 'backend_prediction_unavailable',
    });
  });

  it('preserves legacy backend rejection responses', async () => {
    mocks.nightly.mockReturnValue({ expected_move: { em_ml_pct: .07 } });
    mocks.proxy.mockRejectedValue(new BackendProxyError('No fresh feature snapshot', 404));
    expect((await post(request)).status).toBe(404);
  });
});
