import Link from 'next/link';
import controlPlaneJson from '../public/control-plane.json';

type Status = 'passed' | 'degraded' | 'failed' | 'warning' | 'unavailable' | string;

type ControlPlane = {
  generated_at?: string;
  status?: Status;
  publication_eligible?: boolean;
  data?: {
    status?: Status;
    source_date?: string | null;
    expected_source_date?: string | null;
    event_coverage_pct?: number | null;
    decision_scope?: string | null;
  };
  model?: {
    status?: Status;
    drift_status?: Status;
  };
};

const control = controlPlaneJson as ControlPlane;

function statusColor(status?: Status): string {
  if (status === 'passed') return 'var(--up)';
  if (status === 'failed') return 'var(--down)';
  if (status === 'degraded' || status === 'warning') return 'var(--warn)';
  return 'var(--ink-4)';
}

function label(status?: Status): string {
  if (!status) return 'unavailable';
  return status.replaceAll('_', ' ');
}

function sourceLabel(): string {
  const source = control.data?.source_date;
  const expected = control.data?.expected_source_date;
  if (!source) return 'Snapshot unavailable';
  if (expected && source !== expected) return `Snapshot ${source} · expected ${expected}`;
  return `Snapshot ${source} EOD`;
}

export function ResearchStatusStrip() {
  const overall = control.status ?? 'unavailable';
  const data = control.data?.status ?? 'unavailable';
  const model = control.model?.status ?? 'unavailable';
  const drift = control.model?.drift_status ?? 'unavailable';
  const coverage = control.data?.event_coverage_pct;

  return (
    <div
      role="status"
      aria-label="Current Quantiv research evidence status"
      style={{
        borderBottom: '1px solid var(--line)',
        background: 'color-mix(in oklab, var(--bg-2) 70%, transparent)',
      }}
    >
      <div
        className="qv-m-pad"
        style={{
          maxWidth: 1240,
          minHeight: 28,
          margin: '0 auto',
          padding: '5px 28px',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
          fontSize: 10.5,
          color: 'var(--ink-3)',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background: statusColor(overall),
            }}
          />
          <span className="mono" style={{ color: statusColor(overall), textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Research {label(overall)}
          </span>
        </span>
        <span>{sourceLabel()}</span>
        <span className="qv-m-hide">Data {label(data)}</span>
        <span className="qv-m-hide">Model {label(model)}</span>
        <span className="qv-m-hide" style={{ color: statusColor(drift) }}>
          Drift {label(drift)}
        </span>
        {coverage != null && Number.isFinite(coverage) && (
          <span className="qv-m-hide">Event coverage {(coverage * 100).toFixed(1)}%</span>
        )}
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 10, alignItems: 'center' }}>
          <span className="qv-m-hide">
            {control.publication_eligible ? 'Publication eligible' : 'Publication blocked'}
          </span>
          <Link
            href="/validation"
            style={{ color: 'var(--ink-2)', textDecoration: 'none', whiteSpace: 'nowrap' }}
          >
            Audit evidence →
          </Link>
        </span>
      </div>
    </div>
  );
}
