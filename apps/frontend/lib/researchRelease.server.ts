import { createHash } from 'node:crypto';
import {
  closeSync,
  constants,
  existsSync,
  fstatSync,
  openSync,
  readFileSync,
} from 'node:fs';
import { isAbsolute, join } from 'node:path';
import deploymentPointerJson from '../frontend-release.json';
import { canonicalJson } from './researchSnapshot.server';

const RELEASE_SCHEMA = 'quantiv.frontend-release.v1';
const DEPLOYMENT_SCHEMA = 'quantiv.frontend-deployment.v1';
const SHA256_RE = /^[0-9a-f]{64}$/;

export type RetainedReleaseBinding = {
  status: 'verified';
  release_id: string;
  manifest_sha256: string;
  source_revision: string | null;
  source_artifact: {
    path: string;
    bytes: number;
    sha256: string;
  };
};

type ReleaseFile = {
  path: string;
  bytes: number;
  sha256: string;
};

type ReleaseManifest = {
  schema?: unknown;
  release_id?: unknown;
  files?: unknown;
  file_count?: unknown;
  total_bytes?: unknown;
};

type DeploymentControl = {
  releaseId: string;
  manifestBytes: number;
  manifestSha256: string;
  sourceRevision: string | null;
};

type VerifiedArtifactBytes = {
  binding: RetainedReleaseBinding;
  bytes: Buffer;
};

function sha256(data: Buffer | string): string {
  return createHash('sha256').update(data).digest('hex');
}

function publicRoot(explicit?: string): string {
  if (explicit) return explicit;
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public'),
    join(process.cwd(), 'public'),
  ];
  const root = candidates.find((candidate) => existsSync(candidate));
  if (!root) throw new Error('frontend public directory is unavailable');
  return root;
}

function safeRelativePath(value: unknown, label: string): string {
  if (
    typeof value !== 'string' ||
    !value ||
    value.includes('\\') ||
    isAbsolute(value)
  ) {
    throw new Error(`${label} must be a safe relative path`);
  }
  const parts = value.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..')) {
    throw new Error(`${label} must be a safe relative path`);
  }
  return value;
}

function readRegularFileNoFollow(path: string, label: string): Buffer {
  let descriptor: number | null = null;
  try {
    descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const stat = fstatSync(descriptor);
    if (!stat.isFile()) {
      throw new Error(`${label} must be a regular file`);
    }
    return readFileSync(descriptor);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException | null)?.code;
    if (code === 'ELOOP') {
      throw new Error(`${label} must not be a symbolic link`);
    }
    throw error;
  } finally {
    if (descriptor !== null) closeSync(descriptor);
  }
}

function releaseFiles(value: unknown): ReleaseFile[] {
  if (!Array.isArray(value)) throw new Error('frontend release files must be an array');
  const seen = new Set<string>();
  return value.map((raw, index) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error(`frontend release file ${index} must be an object`);
    }
    const item = raw as Record<string, unknown>;
    const path = safeRelativePath(item.path, `frontend release file ${index} path`);
    if (seen.has(path)) throw new Error(`duplicate frontend release path: ${path}`);
    seen.add(path);
    if (!Number.isInteger(item.bytes) || Number(item.bytes) < 0) {
      throw new Error(`frontend release file ${path} has invalid byte count`);
    }
    if (typeof item.sha256 !== 'string' || !SHA256_RE.test(item.sha256)) {
      throw new Error(`frontend release file ${path} has invalid SHA-256`);
    }
    return { path, bytes: Number(item.bytes), sha256: item.sha256 };
  });
}

function deploymentControl(value: unknown): DeploymentControl {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('frontend deployment pointer must be an object');
  }
  const pointer = value as Record<string, unknown>;
  if (pointer.schema !== DEPLOYMENT_SCHEMA) {
    throw new Error('frontend deployment pointer schema is unsupported');
  }
  if (typeof pointer.release_id !== 'string' || !SHA256_RE.test(pointer.release_id)) {
    throw new Error('frontend deployment release id is invalid');
  }
  if (!pointer.manifest || typeof pointer.manifest !== 'object' || Array.isArray(pointer.manifest)) {
    throw new Error('frontend deployment manifest control is invalid');
  }
  const manifest = pointer.manifest as Record<string, unknown>;
  if (manifest.path !== `manifests/${pointer.release_id}.json`) {
    throw new Error('frontend deployment manifest path does not match release id');
  }
  if (!Number.isInteger(manifest.bytes) || Number(manifest.bytes) <= 0) {
    throw new Error('frontend deployment manifest byte count is invalid');
  }
  if (typeof manifest.sha256 !== 'string' || !SHA256_RE.test(manifest.sha256)) {
    throw new Error('frontend deployment manifest SHA-256 is invalid');
  }
  const sourceRevision =
    typeof pointer.source_revision === 'string' && pointer.source_revision
      ? pointer.source_revision
      : null;
  return {
    releaseId: pointer.release_id,
    manifestBytes: Number(manifest.bytes),
    manifestSha256: manifest.sha256,
    sourceRevision,
  };
}

function verifyRetainedPublicArtifactBytes(
  relativePath: string,
  explicitPublicRoot?: string,
  expectedDeployment: unknown = deploymentPointerJson,
): VerifiedArtifactBytes {
  const sourcePath = safeRelativePath(relativePath, 'retained source artifact path');
  const control = deploymentControl(expectedDeployment);
  const root = publicRoot(explicitPublicRoot);
  const manifestPath = join(root, 'frontend-release-manifest.json');
  const manifestBytes = readRegularFileNoFollow(
    manifestPath,
    'verified frontend release manifest',
  );
  const manifestDigest = sha256(manifestBytes);
  if (manifestBytes.length !== control.manifestBytes) {
    throw new Error('materialized frontend manifest byte count does not match deployment pointer');
  }
  if (manifestDigest !== control.manifestSha256) {
    throw new Error('materialized frontend manifest SHA-256 does not match deployment pointer');
  }

  let manifest: ReleaseManifest;
  try {
    manifest = JSON.parse(manifestBytes.toString('utf8')) as ReleaseManifest;
  } catch {
    throw new Error('frontend release manifest is invalid JSON');
  }
  if (manifest.schema !== RELEASE_SCHEMA) {
    throw new Error('frontend release manifest schema is unsupported');
  }
  if (manifest.release_id !== control.releaseId) {
    throw new Error('materialized frontend release id does not match deployment pointer');
  }

  const files = releaseFiles(manifest.files);
  if (!Number.isInteger(manifest.file_count) || Number(manifest.file_count) !== files.length) {
    throw new Error('frontend release file count does not reconcile');
  }
  const totalBytes = files.reduce((sum, item) => sum + item.bytes, 0);
  if (!Number.isInteger(manifest.total_bytes) || Number(manifest.total_bytes) !== totalBytes) {
    throw new Error('frontend release total bytes do not reconcile');
  }

  const core = {
    schema: RELEASE_SCHEMA,
    files,
    file_count: files.length,
    total_bytes: totalBytes,
  };
  const calculatedReleaseId = sha256(canonicalJson(core));
  if (calculatedReleaseId !== control.releaseId) {
    throw new Error('frontend release id does not match manifest inventory');
  }

  const retained = files.find((item) => item.path === sourcePath);
  if (!retained) {
    throw new Error(`frontend release does not retain ${sourcePath}`);
  }
  const materializedPath = join(root, sourcePath);
  const sourceBytes = readRegularFileNoFollow(
    materializedPath,
    `retained source artifact ${sourcePath}`,
  );
  if (sourceBytes.length !== retained.bytes) {
    throw new Error(`retained source artifact byte count mismatch: ${sourcePath}`);
  }
  if (sha256(sourceBytes) !== retained.sha256) {
    throw new Error(`retained source artifact SHA-256 mismatch: ${sourcePath}`);
  }

  return {
    binding: {
      status: 'verified',
      release_id: control.releaseId,
      manifest_sha256: manifestDigest,
      source_revision: control.sourceRevision,
      source_artifact: retained,
    },
    bytes: sourceBytes,
  };
}

/**
 * Bind one materialized public artifact to the exact immutable frontend release
 * authorized by the Git deployment pointer. Files are opened without following
 * final-component symlinks, validated through the same descriptor they are read
 * from, and hashed from those exact bytes to avoid check/use races.
 */
export function verifyRetainedPublicArtifact(
  relativePath: string,
  explicitPublicRoot?: string,
  expectedDeployment: unknown = deploymentPointerJson,
): RetainedReleaseBinding {
  return verifyRetainedPublicArtifactBytes(
    relativePath,
    explicitPublicRoot,
    expectedDeployment,
  ).binding;
}

/**
 * Parse JSON from the exact source bytes whose size and digest were verified.
 * Callers that use source contents for research decisions should prefer this
 * helper over separately reading and then verifying the same path.
 */
export function readVerifiedRetainedPublicJson<T>(
  relativePath: string,
  explicitPublicRoot?: string,
  expectedDeployment: unknown = deploymentPointerJson,
): { binding: RetainedReleaseBinding; value: T } {
  const verified = verifyRetainedPublicArtifactBytes(
    relativePath,
    explicitPublicRoot,
    expectedDeployment,
  );
  try {
    return {
      binding: verified.binding,
      value: JSON.parse(verified.bytes.toString('utf8')) as T,
    };
  } catch {
    throw new Error(`retained source artifact is invalid JSON: ${relativePath}`);
  }
}
