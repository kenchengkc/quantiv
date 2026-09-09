import { createHash } from 'node:crypto';

const requiredPaths = ['weekly.json', 'screener.json', 'control-plane.json', 'evidence/model-validation.json'];
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');

function verifyBytes(bytes, expected, label) {
  if (!Number.isSafeInteger(expected?.bytes) || expected.bytes <= 0 ||
      !/^[a-f0-9]{64}$/.test(expected?.sha256 ?? '')) {
    throw new Error(`Invalid expected digest/size: ${label}`);
  }
  if (bytes.length !== expected.bytes || digest(bytes) !== expected.sha256) {
    throw new Error(`Published bytes do not match Git-pinned release: ${label}`);
  }
}

// Verify a bounded cross-route sample, not a claim that every public file was
// fetched. Build-time materialization verifies the full archive inventory.
export async function verifyFrontendPublication(baseUrl, pointer, fetcher = fetch) {
  if (pointer?.schema !== 'quantiv.frontend-deployment.v1' ||
      !/^[a-f0-9]{64}$/.test(pointer.release_id ?? '') ||
      pointer.manifest?.path !== `manifests/${pointer.release_id}.json`) {
    throw new Error('Invalid Git frontend deployment pointer');
  }
  async function download(path) {
    const url = new URL(`/${path}`, baseUrl);
    const response = await fetcher(url, {
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`${path} returned ${response.status}`);
    return Buffer.from(await response.arrayBuffer());
  }
  const bytes = await download('frontend-release-manifest.json');
  verifyBytes(bytes, pointer.manifest, 'frontend-release-manifest.json');
  const manifest = JSON.parse(bytes.toString('utf8'));
  if (manifest.schema !== 'quantiv.frontend-release.v1' ||
      manifest.release_id !== pointer.release_id || !Array.isArray(manifest.files)) {
    throw new Error('Published manifest identity does not match Git');
  }
  const inventory = new Map();
  for (const item of manifest.files) {
    if (typeof item.path !== 'string' || inventory.has(item.path) ||
        !/^[A-Za-z0-9_./-]+$/.test(item.path) ||
        item.path.split('/').some((part) => ['', '.', '..'].includes(part))) {
      throw new Error('Unsafe or duplicate publication inventory path');
    }
    inventory.set(item.path, item);
  }
  const paths = [...requiredPaths];
  for (const prefix of ['weeks/', 'symbols/']) {
    const path = [...inventory.keys()].sort().find((name) => name.startsWith(prefix) && name.endsWith('.json'));
    if (!path) throw new Error(`Missing publication inventory family: ${prefix}`);
    paths.push(path);
  }
  await Promise.all(paths.map(async (path) => {
    const expected = inventory.get(path);
    if (!expected) throw new Error(`Missing required publication file: ${path}`);
    verifyBytes(await download(path), expected, path);
  }));
  return { releaseId: pointer.release_id, verifiedPaths: paths };
}
