'use client';

import Link from 'next/link';
import { useCallback, useMemo, useState } from 'react';
import { Check, Copy, Download, History } from 'lucide-react';
import type { ComparableResearchContext } from '@/lib/comparableResearch';

function href(symbol: string, format: 'json' | 'csv'): string {
  const params = new URLSearchParams({ symbol, format });
  return `/api/research/symbol-snapshot?${params.toString()}`;
}

function pct(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

function ratio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value.toFixed(2)}x`;
}

const actionStyle = {
  minHeight: 30,
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

export function ComparableHistoryLink({ context }: { context: ComparableResearchContext }) {
  const summary = context.summary;
  const contextTitle = summary
    ? `${summary.events} eligible historical events across ${summary.symbols} symbols. Median realized move ${pct(summary.medianRealized, 1)}; median realized/implied ${ratio(summary.medianRatio)}; ${pct(summary.outsideRate)} exceeded the priced move. Same report session when known and a ±25% band around the current ${pct(context.currentImplied, 1)} straddle-implied move.`
    : 'Open historical events with a similar pre-earnings implied-move regime and report session';

  return (
    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 12px' }}>
      <Link href={context.href} style={actionStyle} title={contextTitle}>
        <History size={11} aria-hidden />
        Comparable history
      </Link>
      {summary && summary.events > 0 && (
        <span
          title={contextTitle}
          aria-label="Comparable historical calibration summary"
          style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.6 }}
        >
          {summary.events} events across {summary.symbols} symbols · {ratio(summary.medianRatio)} median realized/implied ·{' '}
          {pct(summary.outsideRate)} outside implied
        </span>
      )}
    </div>
  );
}

export default function SymbolResearchExport({
  symbol,
}: {
  symbol: string;
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

  return (
    <div
      aria-label="Symbol research snapshot exports"
      style={{
        marginTop: 12,
        display: 'flex',
        justifyContent: 'flex-end',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '8px 6px',
      }}
    >
      <span
        style={{ marginRight: 'auto', fontSize: 11, color: 'var(--ink-4)' }}
        title="Content-addressed export of the validated end-of-day symbol payload. Live quotes and spot-updated model overlays are excluded."
      >
        Export snapshot
      </span>
      <a href={jsonHref} style={actionStyle} title="Download validated symbol research as JSON">
        <Download size={11} aria-hidden />
        JSON
      </a>
      <a href={csvHref} style={actionStyle} title="Download a one-row CSV research snapshot">
        <Download size={11} aria-hidden />
        CSV
      </a>
      <button
        type="button"
        onClick={() => void copyId()}
        disabled={copying}
        style={{ ...actionStyle, opacity: copying ? 0.65 : 1 }}
        title="Copy the SHA-256 research snapshot identifier"
      >
        {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
        {copied ? 'Copied' : 'Copy ID'}
      </button>
    </div>
  );
}
