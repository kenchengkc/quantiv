'use client';

import Link from 'next/link';
import { useCallback, useMemo, useState } from 'react';
import { Check, Copy, Download, History } from 'lucide-react';

function href(symbol: string, format: 'json' | 'csv'): string {
  const params = new URLSearchParams({ symbol, format });
  return `/api/research/symbol-snapshot?${params.toString()}`;
}

export default function SymbolResearchExport({
  symbol,
  comparableHref,
}: {
  symbol: string;
  comparableHref?: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const [copying, setCopying] = useState(false);
  const jsonHref = useMemo(() => href(symbol, 'json'), [symbol]);
  const csvHref = useMemo(() => href(symbol, 'csv'), [symbol]);

  const copyId = useCallback(async () => {
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
      // Export downloads remain usable if clipboard access is unavailable.
    } finally {
      setCopying(false);
    }
  }, [copying, jsonHref]);

  const style = {
    height: 30,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '0 9px',
    borderRadius: 8,
    border: '1px solid var(--line)',
    background: 'color-mix(in oklab, var(--bg-2) 92%, transparent)',
    color: 'var(--ink-2)',
    fontSize: 10.5,
    textDecoration: 'none',
    cursor: 'pointer',
    whiteSpace: 'nowrap' as const,
  };

  return (
    <div
      className="qv-m-pad"
      aria-label="Symbol research snapshot exports"
      style={{
        maxWidth: 1100,
        margin: '0 auto -38px',
        padding: '10px 28px 0',
        display: 'flex',
        justifyContent: 'flex-end',
        alignItems: 'center',
        gap: 6,
        position: 'relative',
        zIndex: 3,
        pointerEvents: 'none',
      }}
    >
      <span
        className="qv-m-hide"
        style={{ marginRight: 3, fontSize: 10, color: 'var(--ink-4)', pointerEvents: 'auto' }}
        title="Content-addressed export of the validated end-of-day symbol payload. Live quotes and spot-updated model overlays are excluded."
      >
        Research snapshot
      </span>
      {comparableHref && (
        <Link
          href={comparableHref}
          style={{ ...style, pointerEvents: 'auto' }}
          title="Open historical events with a similar pre-earnings implied-move regime and report session"
        >
          <History size={11} aria-hidden />
          Comparable history
        </Link>
      )}
      <a href={jsonHref} style={{ ...style, pointerEvents: 'auto' }} title="Download validated symbol research as JSON">
        <Download size={11} aria-hidden />
        JSON
      </a>
      <a href={csvHref} style={{ ...style, pointerEvents: 'auto' }} title="Download a one-row CSV research snapshot">
        <Download size={11} aria-hidden />
        CSV
      </a>
      <button
        type="button"
        onClick={() => void copyId()}
        disabled={copying}
        style={{ ...style, pointerEvents: 'auto', opacity: copying ? 0.65 : 1 }}
        title="Copy the SHA-256 research snapshot identifier"
      >
        {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
        {copied ? 'Copied' : 'Copy ID'}
      </button>
    </div>
  );
}
