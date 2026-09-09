import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { verifyFrontendPublication } from '../verify_frontend_publication.mjs';

const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

function fixture({ missing, extra, releaseId = 'a'.repeat(64) } = {}) {
  const paths = ['weekly.json', 'screener.json', 'control-plane.json',
    'evidence/model-validation.json', 'weeks/2026-09-07.json', 'symbols/AAPL.json'];
  const bodies = new Map(paths.filter((path) => path !== missing)
    .map((path) => [path, Buffer.from(JSON.stringify({ path }))]));
  const files = [...bodies].map(([path, bytes]) => ({ path, bytes: bytes.length, sha256: hash(bytes) }));
  if (extra) files.push(extra);
  const manifest = Buffer.from(JSON.stringify({
    schema: 'quantiv.frontend-release.v1', release_id: releaseId, files,
  }));
  bodies.set('frontend-release-manifest.json', manifest);
  const pointer = {
    schema: 'quantiv.frontend-deployment.v1', release_id: 'a'.repeat(64),
    manifest: { path: `manifests/${'a'.repeat(64)}.json`, bytes: manifest.length, sha256: hash(manifest) },
  };
  const requested = [];
  const fetcher = async (url, options) => {
    assert.equal(url.origin, 'https://example.test');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.redirect, 'error');
    const path = url.pathname.slice(1);
    requested.push(path);
    return bodies.has(path) ? new Response(bodies.get(path)) : new Response('', { status: 404 });
  };
  return { pointer, bodies, requested, fetcher };
}

test('verifies the pinned manifest and bounded cross-route sample', async () => {
  const f = fixture();
  const result = await verifyFrontendPublication('https://example.test', f.pointer, f.fetcher);
  assert.equal(result.releaseId, f.pointer.release_id);
  assert.equal(result.verifiedPaths.length, 6);
  assert.equal(f.requested.length, 7);
});

test('rejects an old manifest before checking data files', async () => {
  const f = fixture();
  const bytes = f.bodies.get('frontend-release-manifest.json');
  f.bodies.set('frontend-release-manifest.json', Buffer.from(bytes.toString().replace('aaaa', 'bbbb')));
  await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /do not match/);
  assert.equal(f.requested.length, 1);
});

test('rejects mixed-release bytes even when the manifest is current', async () => {
  const f = fixture();
  f.bodies.set('screener.json', Buffer.from('{"path":"screenez.json"}'));
  await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /do not match.*screener/);
});

test('rejects missing deployment attestation', async () => {
  const f = fixture();
  f.bodies.delete('frontend-release-manifest.json');
  await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /returned 404/);
});

test('rejects a pinned manifest with a different release identity', async () => {
  const f = fixture({ releaseId: 'b'.repeat(64) });
  await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /identity/);
});

for (const missing of ['weekly.json', 'symbols/AAPL.json', 'weeks/2026-09-07.json']) {
  test(`rejects incomplete inventory: ${missing}`, async () => {
    const f = fixture({ missing });
    await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /Missing/);
  });
}

for (const path of ['../secret.json', '//other.test/file.json', 'weekly.json']) {
  test(`rejects unsafe or duplicate inventory: ${path}`, async () => {
    const f = fixture({ extra: { path, bytes: 1, sha256: 'a'.repeat(64) } });
    await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /Unsafe or duplicate/);
    assert.equal(f.requested.length, 1);
  });
}

test('rejects invalid local pointer before making requests', async () => {
  const f = fixture();
  f.pointer.manifest.path = 'manifests/latest.json';
  await assert.rejects(verifyFrontendPublication('https://example.test', f.pointer, f.fetcher), /Invalid Git/);
  assert.equal(f.requested.length, 0);
});
