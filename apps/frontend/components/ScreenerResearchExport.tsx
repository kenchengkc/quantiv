'use client';

import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Check, Copy, Download } from 'lucide-react';

function exportHref(search: string, format: 'json' | 'csv'): string {
  const params = new URLSearchParams(search);
  params.set('format', format);
  return `/api/research/screener-snapshot?${params.toString()}`;
}

export default function ScreenerResearchExport() {
  const searchParams = useSearchParams();
  const queryString = searchParams.toString();
  const [copied, setCopied] = useState(false);
  const [copying, setCopying] = useState(false);

  const jsonHref = useMemo(
    () => exportHref(queryString, 'json'),
    [queryString],
  );
  const csvHref = useMemo(
    () => exportHref(queryString, 'csv'),
    [queryString],
  );

  const copySnapshotId = useCallback(async () => {
    if (copying) return;
    setCopying(true);
    try {
      const response = await fetch(jsonHref, { cache: 'no-store' });
      if (!response.ok) return;
      const payload = (await response.json()) as { snapshot_id?: string };
      if (!payload.snapshot_id) return;
      await navigator.clipboard.writeText(payload.snapshot_id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Export links remain available even if clipboard permission is denied.
    } finally {
      setCopying(false);
    }
  }, [copying, jsonHref]);

  const controlStyle = {
    height: 32,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '0 10px',
    borderRadius: 8,
    border: '1px solid var(--line)',
    background: 'color-mix(in oklab, var(--bg-2) 92%, transparent)',
    color: 'var(--ink-2)',
    fontSize: 11,
    textDecoration: 'none',
    cursor: 'pointer',
    whiteSpace: 'nowrap' as const,
  };

  return (
    <div
      aria-label="Research snapshot exports"
      style={{
        display: 'flex',
        justifyContent: 'flex-end',
        alignItems: 'center',
        gap: 6,
        paddingTop: 12,
        marginBottom: -44,
        position: 'relative',
        zIndex: 3,
        pointerEvents: 'none',
      }}
    >
      <span
        className="qv-m-hide"
        style={{
          marginRight: 4,
          fontSize: 10.5,
          color: 'var(--ink-4)',
          pointerEvents: 'auto',
        }}
        title="Content-addressed export of the validated end-of-day screener state. Live quote overlays are intentionally excluded."
      >
        Research snapshot
      </span>
      <a
        href={jsonHref}
        style={{ ...controlStyle, pointerEvents: 'auto' }}
        title="Download a content-addressed JSON snapshot with query and evidence metadata"
      >
        <Download size={12} aria-hidden />
        JSON
      </a>
      <a
        href={csvHref}
        style={{ ...controlStyle, pointerEvents: 'auto' }}
        title="Download the filtered research rows as CSV with the immutable snapshot ID on every row"
      >
        <Download size={12} aria-hidden />
        CSV
      </a>
      <button
        type="button"
        onClick={() => void copySnapshotId()}
        disabled={copying}
        style={{
          ...controlStyle,
          pointerEvents: 'auto',
          opacity: copying ? 0.65 : 1,
        }}
        title="Copy the SHA-256 research snapshot identifier"
      >
        {copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
        {copied ? 'Copied' : 'Copy ID'}
      </button>
    </div>
  );
}
