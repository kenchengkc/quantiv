import { ExternalLink, History } from 'lucide-react';
import {
  compareControlReleases,
  recentControlRuns,
} from './controlHistoryViewModel';
import type {
  ControlHistory,
  ControlHistoryRun,
  ControlStatus,
} from './controlPlaneTypes';

function statusColor(run: ControlHistoryRun): string {
  if (!run.publication_eligible || run.status === 'failed') return 'var(--down)';
  if (run.status === 'degraded') return 'var(--flag)';
  return 'var(--up)';
}

function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

function percent(value: number | null): string {
  return value == null || !Number.isFinite(value)
    ? '—'
    : `${(value * 100).toFixed(1)}%`;
}

function signed(value: number | null, suffix = '', digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const rounded = Math.abs(value) < 0.05 ? 0 : value;
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}${suffix}`;
}

function deltaTone(
  value: number | null,
  lowerIsBetter: boolean,
): ControlStatus | 'neutral' {
  if (value == null || Math.abs(value) < 0.05) return 'neutral';
  const improved = lowerIsBetter ? value < 0 : value > 0;
  return improved ? 'passed' : 'failed';
}

function DeltaMetric({
  label,
  value,
  detail,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  detail: string;
  tone?: ControlStatus | 'neutral';
}) {
  const color =
    tone === 'passed'
      ? 'var(--up)'
      : tone === 'failed'
        ? 'var(--down)'
        : 'var(--ink-2)';
  return (
    <div
      style={{
        minWidth: 0,
        padding: '9px 10px',
        border: '1px solid var(--line)',
        borderRadius: 8,
        background: 'color-mix(in oklab, var(--bg-3) 52%, transparent)',
      }}
    >
      <div
        style={{
          color: 'var(--ink-4)',
          fontSize: 9,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </div>
      <div
        className="mono tnum"
        style={{ color, fontSize: 13, fontWeight: 650, marginTop: 3 }}
      >
        {value}
      </div>
      <div style={{ color: 'var(--ink-4)', fontSize: 9.5, marginTop: 2 }}>
        {detail}
      </div>
    </div>
  );
}

function CoverageTimeline({ runs }: { runs: ControlHistoryRun[] }) {
  const width = 620;
  const height = 116;
  const left = 48;
  const right = 18;
  const top = 16;
  const bottom = 28;
  const plotHeight = height - top - bottom;
  const x = (index: number) =>
    runs.length === 1
      ? width / 2
      : left + (index * (width - left - right)) / (runs.length - 1);
  const y = (coverage: number | null) =>
    coverage == null ? height - bottom : top + (1 - coverage) * plotHeight;
  const points = runs
    .map((run, index) =>
      run.event_coverage_pct == null
        ? null
        : `${x(index)},${y(run.event_coverage_pct)}`,
    )
    .filter((point): point is string => point != null)
    .join(' ');

  return (
    <div style={{ overflowX: 'auto', marginTop: 10 }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Eligible earnings coverage across ${runs.length} retained control snapshots`}
        style={{ display: 'block', width: '100%', minWidth: 520, height: 116 }}
      >
        {[0.5, 0.75, 1].map((level) => (
          <g key={level}>
            <line
              x1={left}
              x2={width - right}
              y1={y(level)}
              y2={y(level)}
              stroke="var(--line)"
              strokeDasharray="2 5"
            />
            <text
              x={left - 8}
              y={y(level) + 3}
              fill="var(--ink-4)"
              fontSize="8"
              textAnchor="end"
            >
              {Math.round(level * 100)}
            </text>
          </g>
        ))}
        {points ? (
          <polyline
            points={points}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        ) : null}
        {runs.map((run, index) => (
          <g key={`${run.generated_at}-${index}`}>
            <circle
              cx={x(index)}
              cy={y(run.event_coverage_pct)}
              r="4"
              fill={statusColor(run)}
              stroke="var(--bg)"
              strokeWidth="2"
            />
            <text
              x={x(index)}
              y={Math.max(10, y(run.event_coverage_pct) - 8)}
              fill="var(--ink-2)"
              fontSize="8.5"
              textAnchor="middle"
            >
              {percent(run.event_coverage_pct)}
            </text>
            <text
              x={x(index)}
              y={height - 7}
              fill="var(--ink-4)"
              fontSize="8.5"
              textAnchor="middle"
            >
              {shortDate(run.generated_at)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export default function ControlReleaseHistory({
  history,
}: {
  history: ControlHistory;
}) {
  const runs = recentControlRuns(history);
  const comparison = compareControlReleases(history);
  const current = comparison.current;
  const previous = comparison.previous;
  const workflow = current?.workflow;
  const workflowUrl = workflow?.url?.startsWith('https://')
    ? workflow.url
    : null;

  if (!current) return null;

  return (
    <div
      style={{
        marginBottom: 12,
        border: '1px solid var(--line)',
        borderRadius: 10,
        padding: '13px 14px 14px',
        background: 'color-mix(in oklab, var(--bg-2) 62%, transparent)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <History size={14} style={{ color: 'var(--accent)' }} />
            <h3 className="serif" style={{ margin: 0, fontSize: 15 }}>
              Publication history
            </h3>
          </div>
          <div style={{ color: 'var(--ink-4)', fontSize: 10.5, marginTop: 4 }}>
            Bounded daily snapshots · newest release compared with the prior run
          </div>
        </div>
        {workflowUrl ? (
          <a
            href={workflowUrl}
            target="_blank"
            rel="noreferrer"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              color: 'var(--accent)',
              fontSize: 10.5,
              textDecoration: 'none',
            }}
          >
            Workflow {workflow?.run_number ? `#${workflow.run_number}` : 'run'}
            <ExternalLink size={11} aria-hidden="true" />
          </a>
        ) : null}
      </div>

      <CoverageTimeline runs={runs} />

      <div
        className="qv-m-2col"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 8,
        }}
      >
        <DeltaMetric
          label="Coverage change"
          value={signed(comparison.coverageDeltaPp, ' pp')}
          detail={previous ? `Now ${percent(current.event_coverage_pct)}` : 'Awaiting prior run'}
          tone={deltaTone(comparison.coverageDeltaPp, false)}
        />
        <DeltaMetric
          label="Missing events"
          value={signed(comparison.missingEventsDelta, '', 0)}
          detail={`${current.missing_events ?? '—'} in latest release`}
          tone={deltaTone(comparison.missingEventsDelta, true)}
        />
        <DeltaMetric
          label="Quote rejection"
          value={signed(comparison.rejectionDeltaPp, ' pp')}
          detail="Change can reflect market quality or stricter filters"
        />
        <DeltaMetric
          label="High-drift features"
          value={signed(comparison.criticalFeaturesDelta, '', 0)}
          detail={`${current.critical_features ?? '—'} in latest release`}
          tone={deltaTone(comparison.criticalFeaturesDelta, true)}
        />
      </div>

      {comparison.newExceptionCodes.length || comparison.resolvedExceptionCodes.length ? (
        <div
          style={{
            display: 'flex',
            gap: 12,
            flexWrap: 'wrap',
            marginTop: 10,
            color: 'var(--ink-4)',
            fontSize: 10,
          }}
        >
          {comparison.newExceptionCodes.length ? (
            <span style={{ color: 'var(--flag)' }}>
              New: {comparison.newExceptionCodes.map((code) => code.replace(/_/g, ' ')).join(' · ')}
            </span>
          ) : null}
          {comparison.resolvedExceptionCodes.length ? (
            <span style={{ color: 'var(--up)' }}>
              Resolved: {comparison.resolvedExceptionCodes.map((code) => code.replace(/_/g, ' ')).join(' · ')}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
