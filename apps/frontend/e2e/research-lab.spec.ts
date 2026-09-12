import { expect, test } from '@playwright/test';

type RetainedRelease = {
  status: 'verified' | 'preview_unverified';
  release_id: string | null;
  manifest_sha256: string | null;
  source_revision: string | null;
  source_artifact: {
    path: string;
    bytes: number;
    sha256: string;
  } | null;
};

type ChartPayload = {
  matching_count: number;
  returned_count: number;
  chart: {
    population_count: number;
    sample_count: number;
    max_points: number;
    sampling: string;
    events: Array<{ ticker: string; date: string }>;
  };
};

function chartIdentities(payload: ChartPayload): string[] {
  return payload.chart.events.map((event) => `${event.ticker}|${event.date}`);
}

test('historical cohort API returns point-in-time eligible evidence', async ({ request }) => {
  const response = await request.get('/api/research/cohort?timing=amc&sort=ratio&dir=desc&limit=8');
  expect(response.ok()).toBeTruthy();

  const payload = (await response.json()) as {
    schema: string;
    snapshot_id: string;
    source: {
      historical_universe_completeness: 'source_level' | 'display_limited';
      retained_release: RetainedRelease;
    };
    decision_scope: string;
    live_trading_eligible: boolean;
    live_quote_overlay_included: boolean;
    matching_count: number;
    returned_count: number;
    chart: ChartPayload['chart'];
    events: Array<{
      implied_quality_status: string;
      implied: number;
      realized_abs: number;
    }>;
  };

  expect(payload.schema).toBe('quantiv.historical-cohort.v1');
  expect(payload.snapshot_id).toMatch(/^sha256:[0-9a-f]{64}$/);
  expect(payload.decision_scope).toBe('end_of_day_research');
  expect(payload.live_trading_eligible).toBe(false);
  expect(payload.live_quote_overlay_included).toBe(false);
  if (payload.source.historical_universe_completeness === 'source_level') {
    expect(payload.source.retained_release.status).toBe('verified');
    expect(payload.source.retained_release.release_id).toMatch(/^[0-9a-f]{64}$/);
    expect(payload.source.retained_release.manifest_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(
      payload.source.retained_release.source_revision === null ||
        payload.source.retained_release.source_revision.length > 0,
    ).toBeTruthy();
    expect(payload.source.retained_release.source_artifact?.path).toBe('research-history.json');
    expect(payload.source.retained_release.source_artifact?.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(response.headers()['x-quantiv-retained-release']).toBe(
      payload.source.retained_release.release_id,
    );
  } else {
    expect(payload.source.retained_release).toEqual({
      status: 'preview_unverified',
      release_id: null,
      manifest_sha256: null,
      source_revision: null,
      source_artifact: null,
    });
    expect(response.headers()['x-quantiv-retained-release']).toBe('preview-unverified');
  }
  expect(payload.returned_count).toBeLessThanOrEqual(8);
  expect(payload.matching_count).toBeGreaterThanOrEqual(payload.returned_count);
  expect(payload.events.length).toBe(payload.returned_count);
  expect(payload.chart.population_count).toBe(payload.matching_count);
  expect(payload.chart.sample_count).toBe(payload.chart.events.length);
  expect(payload.chart.sample_count).toBeLessThanOrEqual(payload.chart.max_points);
  expect(payload.chart.max_points).toBe(350);
  expect(payload.chart.sampling).toBe('sha256_event_identity_lowest_v1');
  for (const event of payload.events) {
    expect(event.implied_quality_status).toBe('decision_eligible_eod');
    expect(event.implied).toBeGreaterThan(0);
    expect(event.realized_abs).toBeGreaterThanOrEqual(0);
  }
});

test('calibration sample is invariant to table sort direction and row limit', async ({ request }) => {
  const first = await request.get('/api/research/cohort?sort=ratio&dir=desc&limit=8');
  const second = await request.get('/api/research/cohort?sort=ticker&dir=asc&limit=20');
  expect(first.ok()).toBeTruthy();
  expect(second.ok()).toBeTruthy();

  const firstJson = (await first.json()) as ChartPayload;
  const secondJson = (await second.json()) as ChartPayload;

  expect(firstJson.returned_count).toBeLessThanOrEqual(8);
  expect(secondJson.returned_count).toBeLessThanOrEqual(20);
  expect(firstJson.matching_count).toBe(secondJson.matching_count);
  expect(firstJson.chart.population_count).toBe(secondJson.chart.population_count);
  expect(firstJson.chart.sample_count).toBe(secondJson.chart.sample_count);
  expect(chartIdentities(firstJson)).toEqual(chartIdentities(secondJson));
});

test('identical cohort query has a stable content id and CSV carries it', async ({ request }) => {
  const query = 'quarter=Q2&outcome=outside&limit=20';
  const first = await request.get(`/api/research/cohort?${query}`);
  const second = await request.get(`/api/research/cohort?${query}`);
  expect(first.ok()).toBeTruthy();
  expect(second.ok()).toBeTruthy();
  const firstJson = (await first.json()) as {
    snapshot_id: string;
    source: { retained_release: RetainedRelease };
  };
  const secondJson = (await second.json()) as { snapshot_id: string };
  expect(firstJson.snapshot_id).toBe(secondJson.snapshot_id);

  const csv = await request.get(`/api/research/cohort?${query}&format=csv`);
  expect(csv.ok()).toBeTruthy();
  expect(csv.headers()['x-quantiv-snapshot-id']).toBe(firstJson.snapshot_id);
  expect(csv.headers()['x-quantiv-retained-release']).toBe(
    firstJson.source.retained_release.release_id ?? 'preview-unverified',
  );
  const text = await csv.text();
  expect(text.split('\n')[0]).toContain('snapshot_id');
});

test('research lab renders historical calibration and event evidence', async ({ page }) => {
  await page.goto('/research?limit=20');
  await expect(page.getByRole('heading', { level: 1, name: /research lab/i })).toBeVisible();
  await expect(page.getByText('Calibration map')).toBeVisible();
  await expect(page.getByTestId('calibration-sample-count')).toContainText(/plotted/);
  await expect(page.getByText('Event evidence')).toBeVisible();
  await expect(page.getByText('Median implied')).toBeVisible();
  await expect(page.getByRole('link', { name: 'CSV' })).toBeVisible();
  await expect(page.getByRole('button', { name: /copy cohort id/i })).toBeVisible();
});
