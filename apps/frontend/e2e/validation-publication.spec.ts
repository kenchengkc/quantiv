import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';

type ControlPublication = {
  generated_at: string;
  data: {
    source_date: string;
    expected_source_date?: string;
    source_session_lag?: number;
  };
};

type ForecastPublication = {
  validated_at: string;
};

function readPublication<T>(...parts: string[]): T {
  const candidates = [
    join(process.cwd(), 'public', ...parts),
    join(process.cwd(), 'apps', 'frontend', 'public', ...parts),
  ];
  const path = candidates.find((candidate) => existsSync(candidate));
  if (!path) {
    throw new Error(`Missing frontend publication fixture: ${parts.join('/')}`);
  }
  return JSON.parse(readFileSync(path, 'utf8')) as T;
}

const control = readPublication<ControlPublication>('control-plane.json');
const forecast = readPublication<ForecastPublication>('evidence', 'forecast.json');

function dateLabel(value: string) {
  return new Date(value).toLocaleString('en-US', {
    month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'America/New_York',
  });
}

function optionsEvidenceLabel(data: ControlPublication['data']) {
  const lag = data.source_session_lag;
  if (lag != null && lag > 0 && data.source_date && data.expected_source_date) {
    return `Snapshot ${data.source_date} is ${lag} market session${lag === 1 ? '' : 's'} behind expected ${data.expected_source_date}`;
  }
  if (lag === 0 && data.source_date && data.expected_source_date) {
    return `Snapshot ${data.source_date} matches the expected ${data.expected_source_date} market session`;
  }
  return `Snapshot ${data.source_date} is the latest assessed options evidence`;
}

test('validation separates latest assessment from retained forecast evidence', async ({ page }) => {
  await page.goto('/validation');
  const publication = page.getByRole('region', { name: 'Research publication and freshness' });
  await expect(publication.getByText(`Last forecast validation: ${dateLabel(forecast.validated_at)} ET`, { exact: true })).toBeVisible();
  await expect(publication.getByText(`Assessment captured: ${dateLabel(control.generated_at)} ET`, { exact: true })).toBeVisible();
  await expect(publication.getByText(`Options evidence assessed: ${optionsEvidenceLabel(control.data)}`, { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Current research status', exact: true })).toHaveCount(0);
  const performance = await page.getByRole('heading', { name: 'Does the model add information?' }).boundingBox();
  const calibration = await page.getByRole('heading', { name: 'Calibration', exact: true }).boundingBox();
  const controls = await publication.boundingBox();
  expect(performance).not.toBeNull();
  expect(calibration).not.toBeNull();
  expect(controls).not.toBeNull();
  expect(performance!.y).toBeLessThan(calibration!.y);
  expect(calibration!.y).toBeLessThan(controls!.y);
});

for (const width of [1440, 768, 390]) {
  test(`validation evidence is readable without clipped content at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/validation');
    await expect(page.getByRole('heading', { name: /research validation/i, level: 1 })).toBeVisible();
    const overflow = await page.evaluate(() => ({
      viewport: innerWidth, root: document.documentElement.scrollWidth, body: document.body.scrollWidth,
    }));
    expect(overflow.root).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.body).toBeLessThanOrEqual(overflow.viewport + 1);
    // Check individual evidence text too: overflow:hidden must not mask clipping.
    const lineage = page.getByRole('heading', { name: 'Evidence behind this page' }).locator('..').locator('..');
    const clipped = await lineage.locator('span').evaluateAll((nodes) => nodes.filter((node) => {
      const rect = node.getBoundingClientRect();
      return rect.left < -1 || rect.right > innerWidth + 1 || node.scrollWidth > node.clientWidth + 1;
    }).map((node) => node.textContent));
    expect(clipped).toEqual([]);
    await testInfo.attach(`validation-${width}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  });
}
