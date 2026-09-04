import { expect, test } from '@playwright/test';

test('historical cohort API returns point-in-time eligible evidence', async ({ request }) => {
  const response = await request.get('/api/research/cohort?timing=amc&sort=ratio&dir=desc&limit=8');
  expect(response.ok()).toBeTruthy();

  const payload = (await response.json()) as {
    schema: string;
    snapshot_id: string;
    decision_scope: string;
    live_trading_eligible: boolean;
    live_quote_overlay_included: boolean;
    matching_count: number;
    returned_count: number;
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
  expect(payload.returned_count).toBeLessThanOrEqual(8);
  expect(payload.matching_count).toBeGreaterThanOrEqual(payload.returned_count);
  expect(payload.events.length).toBe(payload.returned_count);
  for (const event of payload.events) {
    expect(event.implied_quality_status).toBe('decision_eligible_eod');
    expect(event.implied).toBeGreaterThan(0);
    expect(event.realized_abs).toBeGreaterThanOrEqual(0);
  }
});

test('identical cohort query has a stable content id and CSV carries it', async ({ request }) => {
  const query = 'quarter=Q2&outcome=outside&limit=20';
  const first = await request.get(`/api/research/cohort?${query}`);
  const second = await request.get(`/api/research/cohort?${query}`);
  expect(first.ok()).toBeTruthy();
  expect(second.ok()).toBeTruthy();
  const firstJson = (await first.json()) as { snapshot_id: string };
  const secondJson = (await second.json()) as { snapshot_id: string };
  expect(firstJson.snapshot_id).toBe(secondJson.snapshot_id);

  const csv = await request.get(`/api/research/cohort?${query}&format=csv`);
  expect(csv.ok()).toBeTruthy();
  expect(csv.headers()['x-quantiv-snapshot-id']).toBe(firstJson.snapshot_id);
  const text = await csv.text();
  expect(text.split('\n')[0]).toContain('snapshot_id');
});

test('research lab renders historical calibration and event evidence', async ({ page }) => {
  await page.goto('/research?limit=20');
  await expect(page.getByRole('heading', { level: 1, name: /research lab/i })).toBeVisible();
  await expect(page.getByText('Calibration map')).toBeVisible();
  await expect(page.getByText('Event evidence')).toBeVisible();
  await expect(page.getByText('Median implied')).toBeVisible();
  await expect(page.getByRole('link', { name: 'CSV' })).toBeVisible();
  await expect(page.getByRole('button', { name: /copy cohort id/i })).toBeVisible();
});
