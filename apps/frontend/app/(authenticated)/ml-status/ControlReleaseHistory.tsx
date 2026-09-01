import { ExternalLink, History } from 'lucide-react';
import {
  compareControlReleases,
  decisionAvailability,
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

function ControlTimeline({ runs }: { runs: ControlHistoryRun[] }) {
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
  const seriesPoints = (value: (run: ControlHistoryRun) => number | null) =>
    runs
      .map((run, index) => {
        const observation = value(run);
        return observation == null ? null : `${x(index)},${y(observation)}`;
      })
      .filter((point): point is string => point != null)
      .join(' ');
  const coveragePoints = seriesPoints((run) => run.event_coverage_pct);
  const availabilityPoints = seriesPoints(decisionAvailability);

  return (
    <div style={{ overflowX: 'auto', marginTop: 10 }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Earnings coverage and eligible ATM pair availability across ${runs.length} retained control snapshots`}
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
        {coveragePoints ? (
          <polyline
            points={coveragePoints}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        ) : null}
        {availabilityPoints ? (
          <polyline
            points={availabilityPoints}
            fill="none"
            stroke="var(--brand-blue-1)"
            strokeWidth="1.5"
            strokeLinejoin="round"
            strokeDasharray="4 3"
          />
        ) : null}
        {runs.map((run, index) => {
          const availability = decisionAvailability(run);
          return (
          <g key={`${run.generated_at}-${index}`}>
            {run.event_coverage_pct != null ? (
              <circle
                cx={x(index)}
                cy={y(run.event_coverage_pct)}
                r="3.5"
                fill="var(--accent)"
                stroke="var(--bg)"
                strokeWidth="1.5"
              />
            ) : null}
            {availability != null ? (
              <rect
                x={x(index) - 3}
                y={y(availability) - 3}
                width="6"
                height="6"
                rx="1"
                fill="var(--brand-blue-1)"
                stroke="var(--bg)"
                strokeWidth="1.2"
              />
            ) : null}
            <circle
              cx={x(index)}
              cy={height - 20}
              r="2.5"
              fill={statusColor(run)}
            />
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
          );
        })}
      </svg>
      <div
        style={{
          display: 'flex',
          gap: 14,
          flexWrap: 'wrap',
          color: 'var(--ink-4)',
          fontSize: 9,
          marginTop: -4,
          paddingLeft: 48,
        }}
      >
        <span style={{ color: 'var(--accent)' }}>● Earnings coverage</span>
        <span style={{ color: 'var(--brand-blue-1)' }}>■ Eligible ATM pairs</span>
        <span>Bottom dot = release status</span>
      </div>
    </div>
  );
}

function compactStatus(value: string | null | undefined): string {
  if (!value || value === 'unavailable') return 'Not recorded';
  return value.replace(/_/g, ' ');
}

function stateColor(value: string | null | undefined): string {
  if (value === 'passed' || value === 'verified' || value === 'enforced') {
    return 'var(--up)';
  }
  if (value === 'failed' || value === 'critical') return 'var(--down)';
  if (value === 'degraded' || value === 'warning') return 'var(--flag)';
  return 'var(--ink-4)';
}

function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'duration not recorded';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m to controls`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m to controls`;
}

function trigger(value: string | undefined): string {
  if (value === 'schedule') return 'Scheduled';
  if (value === 'workflow_dispatch') return 'Manual';
  return value ? value.replace(/_/g, ' ') : 'Prior format';
}

function RunAuditTable({ runs }: { runs: ControlHistoryRun[] }) {
  const newestFirst = [...runs].reverse();
  return (
    <div style={{ marginTop: 13 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 7,
        }}
      >
        <div style={{ color: 'var(--ink-2)', fontSize: 10.5, fontWeight: 650 }}>
          Recent release audit
        </div>
        <div style={{ color: 'var(--ink-4)', fontSize: 9 }}>
          {newestFirst.length} shown · up to 30 retained
        </div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            minWidth: 850,
            borderCollapse: 'collapse',
            fontSize: 9.5,
          }}
        >
          <thead>
            <tr style={{ color: 'var(--ink-4)', textAlign: 'left' }}>
              {['Run', 'Market data', 'Decision coverage', 'Data controls', 'Model controls', 'Promotion', 'Exceptions'].map((label) => (
                <th
                  key={label}
                  style={{
                    borderBottom: '1px solid var(--line)',
                    padding: '6px 8px',
                    fontWeight: 500,
                    letterSpacing: '0.05em',
                    textTransform: 'uppercase',
                  }}
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {newestFirst.map((run) => {
              const workflowUrl = run.workflow?.url?.startsWith('https://')
                ? run.workflow.url
                : null;
              const availability = decisionAvailability(run);
              const outcomeRows =
                run.outcome_common_rows == null || run.outcome_minimum_rows == null
                  ? null
                  : `${run.outcome_common_rows}/${run.outcome_minimum_rows} paired`;
              return (
                <tr key={run.generated_at}>
                  <td style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}>
                    <div style={{ color: statusColor(run), fontWeight: 650 }}>
                      {workflowUrl ? (
                        <a
                          href={workflowUrl}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: 'inherit', textDecoration: 'none' }}
                        >
                          {shortDate(run.generated_at)} ↗
                        </a>
                      ) : shortDate(run.generated_at)}
                    </div>
                    <div style={{ color: 'var(--ink-4)', marginTop: 2 }}>
                      {trigger(run.workflow?.event_name)} · {duration(run.workflow?.control_ready_seconds)}
                    </div>
                  </td>
                  <td style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}>
                    <div className="mono tnum" style={{ color: 'var(--ink-2)' }}>
                      {run.source_date ?? '—'}
                    </div>
                    <div style={{ color: 'var(--ink-4)', marginTop: 2 }}>
                      {run.source_session_lag == null
                        ? 'lag not recorded'
                        : `${run.source_session_lag} session lag`}
                    </div>
                  </td>
                  <td style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}>
                    <div className="mono tnum" style={{ color: 'var(--accent)' }}>
                      {percent(run.event_coverage_pct)} events
                    </div>
                    <div className="mono tnum" style={{ color: 'var(--brand-blue-1)', marginTop: 2 }}>
                      {percent(availability)} ATM pairs
                    </div>
                  </td>
                  <td style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}>
                    <div style={{ color: stateColor(run.replay_status) }}>
                      Replay {compactStatus(run.replay_status)}
                    </div>
                    <div style={{ color: stateColor(run.corporate_action_status), marginTop: 2 }}>
                      Actions {compactStatus(run.corporate_action_status)}
                    </div>
                    <div style={{ color: 'var(--ink-4)', marginTop: 2 }}>
                      {run.quarantine_records ?? '—'} quarantined · {run.duplicate_rows ?? '—'} duplicate
                    </div>
                  </td>
                  <td style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}>
                    <div style={{ color: stateColor(run.model_status) }}>
                      Model {compactStatus(run.model_status)}
                    </div>
                    <div style={{ color: stateColor(run.drift_status), marginTop: 2 }}>
                      Drift {compactStatus(run.drift_status)}
                    </div>
                    <div style={{ color: 'var(--ink-4)', marginTop: 2 }}>
                      Outcome {compactStatus(run.outcome_status)}{outcomeRows ? ` · ${outcomeRows}` : ''}
                    </div>
                  </td>
                  <td style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}>
                    <div style={{ color: stateColor(run.artifact_promotion_status) }}>
                      Artifacts {compactStatus(run.artifact_promotion_status)}
                    </div>
                    <div style={{ color: stateColor(run.neon_import_status), marginTop: 2 }}>
                      Neon {compactStatus(run.neon_import_status)}
                    </div>
                  </td>
                  <td
                    title={run.exception_codes.join(', ')}
                    style={{ borderBottom: '1px solid var(--line)', padding: '8px' }}
                  >
                    <div className="mono tnum" style={{ color: run.critical_exceptions ? 'var(--down)' : 'var(--ink-2)' }}>
                      {run.critical_exceptions} critical
                    </div>
                    <div className="mono tnum" style={{ color: run.warning_exceptions ? 'var(--flag)' : 'var(--ink-4)', marginTop: 2 }}>
                      {run.warning_exceptions} warning
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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
            Static 30-release audit · newest controls compared with the prior run
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

      <ControlTimeline runs={runs} />

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
          label="ATM-pair availability"
          value={signed(comparison.decisionAvailabilityDeltaPp, ' pp')}
          detail={`Now ${percent(decisionAvailability(current))}`}
          tone={deltaTone(comparison.decisionAvailabilityDeltaPp, false)}
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

      <RunAuditTable runs={runs} />
    </div>
  );
}
