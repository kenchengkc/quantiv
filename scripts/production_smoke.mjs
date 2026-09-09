#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { verifyFrontendPublication } from './verify_frontend_publication.mjs';

const frontendUrl = process.env.PRODUCTION_FRONTEND_URL ?? 'https://usequantiv.com';
const backendUrl = process.env.PRODUCTION_BACKEND_URL ?? 'https://api.usequantiv.com';
const attempts = Number(process.env.SMOKE_ATTEMPTS ?? 12);
const delayMs = Number(process.env.SMOKE_DELAY_MS ?? 15_000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function eventually(label, check, maxAttempts = attempts) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      await check();
      console.log(`ok: ${label}`);
      return;
    } catch (error) {
      lastError = error;
      console.warn(`${label}: attempt ${attempt}/${maxAttempts} failed: ${error.message}`);
      if (attempt < maxAttempts) await sleep(delayMs);
    }
  }
  throw new Error(`${label} failed after ${maxAttempts} attempts`, { cause: lastError });
}

async function fetchOk(url, init) {
  const response = await fetch(url, {
    ...init,
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response;
}

const deploymentPointer = JSON.parse(await readFile(
  new URL('../apps/frontend/frontend-release.json', import.meta.url), 'utf8',
));
await eventually('Git-pinned frontend publication', async () => {
  const result = await verifyFrontendPublication(frontendUrl, deploymentPointer);
  console.log(`release ${result.releaseId}: ${result.verifiedPaths.length} public files verified`);
}, Number(process.env.SMOKE_RELEASE_ATTEMPTS ?? 40));

await eventually('frontend shell and security headers', async () => {
  const response = await fetchOk(`${frontendUrl}/`);
  const expectedHeaders = {
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'SAMEORIGIN',
    'referrer-policy': 'strict-origin-when-cross-origin',
    'permissions-policy': 'camera=(), geolocation=(), microphone=()',
    'x-dns-prefetch-control': 'off',
  };
  for (const [name, expected] of Object.entries(expectedHeaders)) {
    if (response.headers.get(name) !== expected) {
      throw new Error(`${name} is missing or unexpected`);
    }
  }
  if (!response.headers.get('strict-transport-security')?.includes('max-age=')) {
    throw new Error('strict-transport-security is missing');
  }
});

await eventually('frontend data contract', async () => {
  const response = await fetchOk(`${frontendUrl}/screener.json`, {
    headers: { accept: 'application/json' },
  });
  const payload = await response.json();
  if (payload?.metadata?.version !== 'v1' || !Array.isArray(payload?.events)) {
    throw new Error('screener.json has an unexpected shape');
  }
  if (payload.metadata.event_count !== payload.events.length || payload.events.length === 0) {
    throw new Error('screener.json event count is invalid');
  }
});

await eventually('backend health', async () => {
  const response = await fetchOk(`${backendUrl}/health`, {
    headers: { accept: 'application/json' },
  });
  const payload = await response.json();
  if (!['healthy', 'degraded'].includes(payload?.status)) {
    throw new Error(`unexpected health status: ${payload?.status ?? 'missing'}`);
  }
});

await eventually('backend documentation is not public', async () => {
  const response = await fetch(`${backendUrl}/openapi.json`, {
    headers: { accept: 'application/json' },
    signal: AbortSignal.timeout(10_000),
  });
  if (response.status !== 401 && response.status !== 404) {
    throw new Error(`${backendUrl}/openapi.json returned ${response.status}`);
  }
});
