#!/usr/bin/env node

const frontendUrl = process.env.PRODUCTION_FRONTEND_URL ?? 'https://usequantiv.com';
const backendUrl = process.env.PRODUCTION_BACKEND_URL ?? 'https://api.usequantiv.com';
const attempts = Number(process.env.SMOKE_ATTEMPTS ?? 12);
const delayMs = Number(process.env.SMOKE_DELAY_MS ?? 15_000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function eventually(label, check) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await check();
      console.log(`ok: ${label}`);
      return;
    } catch (error) {
      lastError = error;
      console.warn(`${label}: attempt ${attempt}/${attempts} failed: ${error.message}`);
      if (attempt < attempts) await sleep(delayMs);
    }
  }
  throw new Error(`${label} failed after ${attempts} attempts`, { cause: lastError });
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

await eventually('frontend shell and security headers', async () => {
  const response = await fetchOk(`${frontendUrl}/`);
  if (response.headers.get('x-content-type-options') !== 'nosniff') {
    throw new Error('X-Content-Type-Options is missing');
  }
  if (response.headers.get('x-frame-options') !== 'SAMEORIGIN') {
    throw new Error('X-Frame-Options is missing');
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
