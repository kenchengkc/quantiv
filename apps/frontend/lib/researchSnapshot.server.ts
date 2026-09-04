import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

export function readPublicJson<T>(...parts: string[]): T | null {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public', ...parts),
    join(process.cwd(), 'public', ...parts),
  ];
  const path = candidates.find((candidate) => existsSync(candidate));
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as T;
  } catch {
    return null;
  }
}

export function canonicalJson(value: unknown): string {
  if (value === undefined) return 'null';
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value) ?? 'null';
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  }
  const object = value as Record<string, unknown>;
  const entries = Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`);
  return `{${entries.join(',')}}`;
}

export function researchSnapshotId(body: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalJson(body)).digest('hex')}`;
}

export function csvCell(value: unknown): string {
  if (value == null) return '';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function downloadHeaders(
  id: string,
  decisionScope: string,
  filename: string,
  contentType: string,
): Headers {
  return new Headers({
    'Cache-Control': 'private, no-store',
    'Content-Type': contentType,
    'Content-Disposition': `attachment; filename="${filename}"`,
    'X-Quantiv-Snapshot-Id': id,
    'X-Quantiv-Decision-Scope': decisionScope,
  });
}
