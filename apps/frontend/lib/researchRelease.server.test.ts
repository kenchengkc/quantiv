import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { verifyRetainedPublicArtifact } from './researchRelease.server';

function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => canonical(item)).join(',')}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonical(object[key])}`)
    .join(',')}}`;
}

function hash(data: Buffer | string): string {
  return createHash('sha256').update(data).digest('hex');
}

type Deployment = {
  schema: 'quantiv.frontend-deployment.v1';
  release_id: string;
  manifest: { path: string; bytes: number; sha256: string };
  source_revision: string;
};

function deploymentFor(releaseId: string, manifestBytes: Buffer): Deployment {
  return {
    schema: 'quantiv.frontend-deployment.v1',
    release_id: releaseId,
    manifest: {
      path: `manifests/${releaseId}.json`,
      bytes: manifestBytes.length,
      sha256: hash(manifestBytes),
    },
    source_revision: 'fixture-source-revision',
  };
}

function writeManifest(root: string, manifest: Record<string, unknown>): Buffer {
  const bytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  writeFileSync(join(root, 'frontend-release-manifest.json'), bytes);
  return bytes;
}

function fixture(): { root: string; source: Buffer; deployment: Deployment } {
  const root = mkdtempSync(join(tmpdir(), 'quantiv-retained-release-'));
  const source = Buffer.from('{"schema":"quantiv.historical-event-universe.v1"}\n');
  writeFileSync(join(root, 'research-history.json'), source);

  const files = [
    {
      path: 'research-history.json',
      bytes: source.length,
      sha256: hash(source),
    },
  ];
  const core = {
    schema: 'quantiv.frontend-release.v1',
    files,
    file_count: files.length,
    total_bytes: source.length,
  };
  const releaseId = hash(canonical(core));
  const manifest = {
    release_id: releaseId,
    ...core,
    archive: {
      path: `releases/${releaseId}.tar.gz`,
      bytes: 123,
      sha256: 'a'.repeat(64),
    },
  };
  const manifestBytes = writeManifest(root, manifest);
  return { root, source, deployment: deploymentFor(releaseId, manifestBytes) };
}

describe('verifyRetainedPublicArtifact', () => {
  it('binds materialized source bytes to the Git-authorized immutable release', () => {
    const { root, source, deployment } = fixture();
    const binding = verifyRetainedPublicArtifact(
      'research-history.json',
      root,
      deployment,
    );

    expect(binding.status).toBe('verified');
    expect(binding.release_id).toBe(deployment.release_id);
    expect(binding.manifest_sha256).toBe(deployment.manifest.sha256);
    expect(binding.source_revision).toBe('fixture-source-revision');
    expect(binding.source_artifact).toEqual({
      path: 'research-history.json',
      bytes: source.length,
      sha256: hash(source),
    });
  });

  it('fails closed when the materialized source changes after release sealing', () => {
    const { root, deployment } = fixture();
    writeFileSync(join(root, 'research-history.json'), '{"tampered":true}\n');

    expect(() =>
      verifyRetainedPublicArtifact('research-history.json', root, deployment),
    ).toThrow(/byte count mismatch|SHA-256 mismatch/);
  });

  it('fails closed when materialized manifest bytes differ from the Git pointer', () => {
    const { root, deployment } = fixture();
    const path = join(root, 'frontend-release-manifest.json');
    const manifest = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
    manifest.total_bytes = Number(manifest.total_bytes) + 1;
    writeManifest(root, manifest);

    expect(() =>
      verifyRetainedPublicArtifact('research-history.json', root, deployment),
    ).toThrow(/manifest (byte count|SHA-256) does not match deployment pointer/);
  });

  it('fails closed when an authorized manifest has a stale release id', () => {
    const { root, deployment } = fixture();
    const path = join(root, 'frontend-release-manifest.json');
    const manifest = JSON.parse(readFileSync(path, 'utf8')) as {
      files: Array<{ path: string; bytes: number; sha256: string }>;
      [key: string]: unknown;
    };
    manifest.files[0].path = 'renamed-research-history.json';
    const manifestBytes = writeManifest(root, manifest);
    const authorizedTamper: Deployment = {
      ...deployment,
      manifest: {
        ...deployment.manifest,
        bytes: manifestBytes.length,
        sha256: hash(manifestBytes),
      },
    };

    expect(() =>
      verifyRetainedPublicArtifact('research-history.json', root, authorizedTamper),
    ).toThrow(/release id does not match manifest inventory/);
  });

  it('fails closed when the authorized retained release omits research history', () => {
    const { root } = fixture();
    const path = join(root, 'frontend-release-manifest.json');
    const manifest = JSON.parse(readFileSync(path, 'utf8')) as {
      files: Array<{ path: string; bytes: number; sha256: string }>;
      schema: string;
      file_count: number;
      total_bytes: number;
      [key: string]: unknown;
    };
    manifest.files[0].path = 'other.json';
    const core = {
      schema: manifest.schema,
      files: manifest.files,
      file_count: manifest.file_count,
      total_bytes: manifest.total_bytes,
    };
    const releaseId = hash(canonical(core));
    manifest.release_id = releaseId;
    const manifestBytes = writeManifest(root, manifest);
    const deployment = deploymentFor(releaseId, manifestBytes);

    expect(() =>
      verifyRetainedPublicArtifact('research-history.json', root, deployment),
    ).toThrow(/does not retain research-history\.json/);
  });
});
