import { expect, test } from '@playwright/test';
import budget from '../performance-budget.json';

function percentile(samples: number[], value: number): number {
  const sorted = [...samples].sort((left, right) => left - right);
  const index = Math.max(0, Math.ceil((value / 100) * sorted.length) - 1);
  return sorted[index];
}

test('homepage cold-load FCP stays inside the production budget', async ({
  browser,
}) => {
  const samples: number[] = [];

  for (let run = 0; run < budget.firstContentfulPaint.sampleCount; run += 1) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const session = await context.newCDPSession(page);
    await session.send('Network.setCacheDisabled', { cacheDisabled: true });

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() =>
      performance
        .getEntriesByType('paint')
        .some((entry) => entry.name === 'first-contentful-paint'),
    );
    samples.push(
      await page.evaluate(
        () =>
          performance
            .getEntriesByType('paint')
            .find((entry) => entry.name === 'first-contentful-paint')
            ?.startTime ?? Infinity,
      ),
    );
    await context.close();
  }

  const p90 = percentile(samples, 90);
  // Seven samples cannot estimate a true p99; the coldest sample is the
  // conservative lab proxy. Production p99 remains a Speed Insights RUM SLO.
  const p99Proxy = Math.max(...samples);
  const evidence = `FCP samples: ${samples.map((sample) => `${Math.round(sample)}ms`).join(', ')}`;
  console.info(
    `${evidence}; p90=${Math.round(p90)}ms; p99-proxy=${Math.round(p99Proxy)}ms`,
  );

  expect(p90, evidence).toBeLessThanOrEqual(budget.firstContentfulPaint.p90Ms);
  expect(p99Proxy, evidence).toBeLessThanOrEqual(
    budget.firstContentfulPaint.p99Ms,
  );
});

test('public landing route does not load Clerk in the critical path', async ({
  page,
}) => {
  await page.goto('/', { waitUntil: 'networkidle' });
  const clerkResources = await page.evaluate(() =>
    performance
      .getEntriesByType('resource')
      .map((entry) => entry.name)
      .filter((name) => name.toLowerCase().includes('clerk')),
  );

  expect(clerkResources).toEqual([]);
});
