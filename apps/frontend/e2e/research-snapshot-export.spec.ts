import { expect, test } from '@playwright/test';

test('screener exposes research snapshot export controls', async ({ page }) => {
  await page.goto('/screener?minSpot=15&sort=hist_edge&dir=desc');

  await expect(page.getByText('Research snapshot')).toBeVisible();
  await expect(page.getByRole('link', { name: 'JSON' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'CSV' })).toBeVisible();
  await expect(page.getByRole('button', { name: /copy id/i })).toBeVisible();
});

test('same validated screener state produces the same content-addressed id', async ({ request }) => {
  const path = '/api/research/screener-snapshot?minSpot=15&sort=hist_edge&dir=desc&format=json';
  const first = await request.get(path);
  const second = await request.get(path);

  expect(first.ok()).toBeTruthy();
  expect(second.ok()).toBeTruthy();

  const firstPayload = (await first.json()) as {
    schema: string;
    snapshot_id: string;
    decision_scope: string;
    live_quote_overlay_included: boolean;
    result_count: number;
    events: unknown[];
  };
  const secondPayload = (await second.json()) as { snapshot_id: string };

  expect(firstPayload.schema).toBe('quantiv.research-snapshot.v1');
  expect(firstPayload.snapshot_id).toMatch(/^sha256:[0-9a-f]{64}$/);
  expect(firstPayload.snapshot_id).toBe(secondPayload.snapshot_id);
  expect(first.headers()['x-quantiv-snapshot-id']).toBe(firstPayload.snapshot_id);
  expect(firstPayload.decision_scope).toBe('end_of_day_research');
  expect(firstPayload.live_quote_overlay_included).toBe(false);
  expect(firstPayload.events).toHaveLength(firstPayload.result_count);
});

test('screener csv export carries the immutable id into each row', async ({ request }) => {
  const response = await request.get(
    '/api/research/screener-snapshot?minSpot=15&preset=rich_vol&format=csv',
  );

  expect(response.ok()).toBeTruthy();
  expect(response.headers()['content-type']).toContain('text/csv');
  const id = response.headers()['x-quantiv-snapshot-id'];
  expect(id).toMatch(/^sha256:[0-9a-f]{64}$/);

  const text = await response.text();
  const [header, firstRow] = text.trim().split('\n');
  expect(header).toContain('snapshot_id');
  if (firstRow) expect(firstRow).toContain(id);
});

test('symbol pages expose the same content-addressed research controls', async ({ page }) => {
  await page.goto('/AAPL');

  const exports = page.getByLabel('Symbol research snapshot exports');
  await expect(exports).toBeVisible();
  await expect(exports.getByRole('link', { name: 'JSON' })).toBeVisible();
  await expect(exports.getByRole('link', { name: 'CSV' })).toBeVisible();
  await expect(exports.getByRole('button', { name: /copy id/i })).toBeVisible();
});

test('symbol snapshot is deterministic and excludes ephemeral overlays', async ({ request }) => {
  const path = '/api/research/symbol-snapshot?symbol=AAPL&format=json';
  const first = await request.get(path);
  const second = await request.get(path);

  expect(first.ok()).toBeTruthy();
  expect(second.ok()).toBeTruthy();

  const payload = (await first.json()) as {
    schema: string;
    kind: string;
    snapshot_id: string;
    decision_scope: string;
    live_quote_overlay_included: boolean;
    spot_updated_prediction_included: boolean;
    research: { symbol?: string };
  };
  const repeated = (await second.json()) as { snapshot_id: string };

  expect(payload.schema).toBe('quantiv.research-snapshot.v1');
  expect(payload.kind).toBe('symbol_research');
  expect(payload.research.symbol).toBe('AAPL');
  expect(payload.snapshot_id).toMatch(/^sha256:[0-9a-f]{64}$/);
  expect(payload.snapshot_id).toBe(repeated.snapshot_id);
  expect(first.headers()['x-quantiv-snapshot-id']).toBe(payload.snapshot_id);
  expect(payload.decision_scope).toBe('end_of_day_research');
  expect(payload.live_quote_overlay_included).toBe(false);
  expect(payload.spot_updated_prediction_included).toBe(false);
});

test('symbol snapshot rejects unsafe ticker input', async ({ request }) => {
  const response = await request.get(
    '/api/research/symbol-snapshot?symbol=../../etc/passwd&format=json',
  );
  expect(response.status()).toBe(400);
});
