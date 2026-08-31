import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  GitCompareArrows,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react';
import ControlReleaseHistory from './ControlReleaseHistory';
import type {
  ControlHistory,
  ControlSnapshot,
  ControlStatus,
} from './controlPlaneTypes';

function statusColor(status: ControlStatus | string): string {
  if (status === 'passed' || status === 'enforced') return 'var(--up)';
  if (status === 'degraded' || status === 'warning' || status === 'verified')
    return 'var(--flag)';
  if (status === 'failed' || status === 'critical') return 'var(--down)';
  return 'var(--ink-3)';
}

function statusLabel(status: ControlStatus | string): string {
  if (status === 'passed') return 'Operational';
  if (status === 'degraded') return 'Needs review';
  if (status === 'failed') return 'Blocked';
  if (status === 'unavailable') return 'Not reported';
  if (status === 'insufficient_data') return 'Building evidence';
  if (status === 'enforced' || status === 'verified') return 'Verified';
  return status.replace(/_/g, ' ');
}

function percent(value: number | null): string {
  return value == null || !Number.isFinite(value)
    ? '—'
    : `${(value * 100).toFixed(1)}%`;
}

function count(value: number | null): string {
  return value == null || !Number.isFinite(value)
    ? '—'
    : value.toLocaleString('en-US');
}

function ControlPill({
  status,
  children,
}: {
  status: ControlStatus | string;
  children: React.ReactNode;
}) {
  const color = statusColor(status);
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        minHeight: 22,
        padding: '2px 8px',
        borderRadius: 999,
        border: `1px solid color-mix(in oklab, ${color} 34%, transparent)`,
        background: `color-mix(in oklab, ${color} 10%, transparent)`,
        color,
        fontSize: 10,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

function ControlMetric({
  label,
  value,
  detail,
  status,
}: {
  label: string;
  value: string;
  detail?: string;
  status?: ControlStatus | string;
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 12,
        alignItems: 'baseline',
        padding: '9px 0',
        borderBottom: '1px solid var(--line)',
      }}
    >
      <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{label}</span>
      <span style={{ textAlign: 'right', minWidth: 0 }}>
        <strong
          className="mono tnum"
          style={{
            color: status ? statusColor(status) : 'var(--ink)',
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          {value}
        </strong>
        {detail ? (
          <small
            style={{
              display: 'block',
              color: 'var(--ink-4)',
              fontSize: 10,
              marginTop: 2,
            }}
          >
            {detail}
          </small>
        ) : null}
      </span>
    </div>
  );
}

function ControlCard({
  title,
  icon,
  status,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  status: ControlStatus;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 10,
        padding: '14px 16px 12px',
        background: 'color-mix(in oklab, var(--bg-2) 62%, transparent)',
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 2,
        }}
      >
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            color: 'var(--ink)',
          }}
        >
          <span style={{ color: 'var(--accent)', display: 'inline-flex' }}>
            {icon}
          </span>
          <h3
            className="serif"
            style={{ margin: 0, fontSize: 16, fontWeight: 700 }}
          >
            {title}
          </h3>
        </div>
        <ControlPill status={status}>{statusLabel(status)}</ControlPill>
      </div>
      {children}
    </div>
  );
}

export default function ProductionControlPanel({
  snapshot,
  history,
}: {
  snapshot: ControlSnapshot;
  history: ControlHistory;
}) {
  const { data, model } = snapshot;
  const publicationEligible =
    snapshot.publication_eligible ?? snapshot.decision_safe ?? false;
  const coveredEventsOnly =
    publicationEligible &&
    data.missing_events != null &&
    data.missing_events > 0;
  const overallLabel = publicationEligible
    ? coveredEventsOnly
      ? 'Publication eligible · covered events only'
      : 'Publication eligible'
    : snapshot.status === 'unavailable'
      ? 'Controls not reported'
      : 'Publication blocked';
  const overallColor = publicationEligible
    ? snapshot.status === 'degraded'
      ? 'var(--flag)'
      : 'var(--up)'
    : snapshot.status === 'unavailable'
      ? 'var(--ink-3)'
      : 'var(--down)';
  const snapshotUnavailable = snapshot.status === 'unavailable';
  const decisionGroupAvailability =
    data.decision_group_rejection_rate == null
      ? null
      : 1 - data.decision_group_rejection_rate;
  const quoteAvailability =
    decisionGroupAvailability ??
    (data.contract_rejection_rate == null
      ? null
      : 1 - data.contract_rejection_rate);

  return (
    <section
      aria-labelledby="production-controls-title"
      style={{
        marginTop: 22,
        border: '1px solid var(--line-2)',
        borderRadius: 12,
        padding: '16px 18px 18px',
        background: 'color-mix(in oklab, var(--bg-2) 42%, transparent)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          marginBottom: 14,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              color: overallColor,
              fontSize: 11,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
            }}
          >
            {publicationEligible ? (
              <ShieldCheck size={15} />
            ) : (
              <AlertTriangle size={15} />
            )}
            <span>{overallLabel}</span>
          </div>
          <h2
            id="production-controls-title"
            className="serif"
            style={{ margin: '7px 0 0', fontSize: 22, fontWeight: 800 }}
          >
            Production controls
          </h2>
          <p
            style={{
              margin: '5px 0 0',
              color: 'var(--ink-3)',
              fontSize: 12,
              lineHeight: 1.45,
            }}
          >
            {publicationEligible
              ? `Covered forecasts cleared publication gates. ${data.live_trading_eligible ? 'Live-trading controls are eligible.' : 'This is end-of-day research data, not an execution feed.'}`
              : 'One exception-first view of data readiness and model controls. Detailed receipts stay in pipeline artifacts.'}
          </p>
        </div>
        <ControlPill status={snapshot.status}>
          {statusLabel(snapshot.status)}
        </ControlPill>
      </div>

      <ControlReleaseHistory history={history} />

      <div
        className="qv-m-stack"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
          gap: 12,
        }}
      >
        <ControlCard
          title="Decision data"
          icon={<Database size={16} />}
          status={data.status}
        >
          <ControlMetric
            label="Latest source"
            value={data.source_date ?? '—'}
            detail={
              data.source_session_lag == null
                ? undefined
                : `${data.source_session_lag} market session${data.source_session_lag === 1 ? '' : 's'} behind expected`
            }
            status={
              data.source_session_lag != null && data.source_session_lag > 1
                ? 'degraded'
                : undefined
            }
          />
          <ControlMetric
            label="Eligible earnings coverage"
            value={percent(data.event_coverage_pct)}
            detail={
              data.expected_events == null
                ? undefined
                : `${count(data.covered_events)} of ${count(data.expected_events)} events`
            }
            status={
              data.event_coverage_pct != null && data.event_coverage_pct < 0.95
                ? 'degraded'
                : 'passed'
            }
          />
          <ControlMetric
            label="Decision scope"
            value={
              data.live_trading_eligible
                ? 'Live trading eligible'
                : 'End-of-day research'
            }
            detail={
              data.live_trading_eligible
                ? 'Timeliness and liquidity controls passed'
                : 'Not eligible as an execution feed'
            }
            status={data.live_trading_eligible ? 'passed' : 'degraded'}
          />
          <ControlMetric
            label={
              decisionGroupAvailability == null
                ? 'Contract availability'
                : 'ATM-pair availability'
            }
            value={percent(quoteAvailability)}
            detail={
              data.decision_groups == null
                ? `${count(data.eligible_contracts)} of ${count(data.contracts)} contracts`
                : `${count(data.eligible_decision_groups ?? null)} of ${count(data.decision_groups)} symbol-expiry sets`
            }
            status={
              quoteAvailability == null
                ? undefined
                : quoteAvailability >=
                    (decisionGroupAvailability == null ? 0.35 : 0.5)
                  ? 'passed'
                  : 'failed'
            }
          />
          <div
            style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 11 }}
          >
            <ControlPill status={data.quarantine_status}>
              Quarantine {count(data.quarantine_records)}
            </ControlPill>
            <ControlPill status={data.replay_status}>
              Replay {statusLabel(data.replay_status)}
            </ControlPill>
            <ControlPill status={data.corporate_action_status}>
              Actions {statusLabel(data.corporate_action_status)}
            </ControlPill>
            {data.duplicate_rows > 0 ? (
              <ControlPill status="failed">
                Duplicates {count(data.duplicate_rows)}
              </ControlPill>
            ) : null}
          </div>
        </ControlCard>

        <ControlCard
          title="Model controls"
          icon={<GitCompareArrows size={16} />}
          status={model.status}
        >
          <ControlMetric
            label="Champion"
            value={model.champion_active ? 'Active' : 'Not reported'}
            detail={
              model.snapshot_date ? `Scored ${model.snapshot_date}` : undefined
            }
            status={model.champion_active ? 'passed' : 'unavailable'}
          />
          <ControlMetric
            label="Feature drift"
            value={statusLabel(model.drift_status)}
            detail={`${count(model.critical_features)} high-drift · ${count(model.warning_features)} warnings`}
            status={model.drift_status}
          />
          <ControlMetric
            label="Shadow scoring"
            value={
              model.shadow_roles.length
                ? model.shadow_roles.join(' · ')
                : 'None reported'
            }
            detail={
              model.challenger_present ? 'Challenger present' : 'No challenger'
            }
            status={model.shadow_roles.length ? 'passed' : 'unavailable'}
          />
          <ControlMetric
            label="Realized outcomes"
            value={statusLabel(model.outcome_status)}
            detail={
              model.outcome_common_rows == null ||
              model.outcome_minimum_rows == null
                ? 'No verified evaluation yet'
                : `${count(model.outcome_common_rows)} of ${count(model.outcome_minimum_rows)} paired events · ${count(model.outcome_evaluations ?? 0)} checks retained`
            }
            status={model.outcome_status}
          />
          <div
            style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 11 }}
          >
            <ControlPill
              status={
                model.fallback_bundle_available ? 'passed' : 'unavailable'
              }
            >
              <RotateCcw size={11} style={{ marginRight: 4 }} />
              {model.fallback_bundle_available
                ? 'Fallback bundle available'
                : 'No fallback bundle reported'}
            </ControlPill>
            {model.rollback_recorded ? (
              <ControlPill status="warning">Rollback recorded</ControlPill>
            ) : null}
          </div>
        </ControlCard>
      </div>

      <div
        style={{
          marginTop: 14,
          paddingTop: 12,
          borderTop: '1px solid var(--line)',
          display: 'flex',
          alignItems: 'flex-start',
          gap: 8,
        }}
      >
        {snapshot.exceptions.length === 0 && !snapshotUnavailable ? (
          <CheckCircle2
            size={14}
            style={{ color: 'var(--up)', flexShrink: 0, marginTop: 1 }}
          />
        ) : (
          <Activity
            size={14}
            style={{ color: 'var(--flag)', flexShrink: 0, marginTop: 1 }}
          />
        )}
        <div style={{ minWidth: 0 }}>
          <div style={{ color: 'var(--ink-2)', fontSize: 12, fontWeight: 600 }}>
            {snapshotUnavailable
              ? 'Control snapshot unavailable'
              : snapshot.exceptions.length
                ? 'Exceptions requiring attention'
                : 'No publication exceptions reported'}
          </div>
          {snapshotUnavailable ? (
            <div style={{ color: 'var(--ink-4)', fontSize: 11, marginTop: 3 }}>
              The next successful data refresh will publish the first
              production-control snapshot.
            </div>
          ) : snapshot.exceptions.length ? (
            <div style={{ display: 'grid', gap: 5, marginTop: 7 }}>
              {snapshot.exceptions.slice(0, 5).map((exception) => (
                <div
                  key={exception.code}
                  style={{
                    display: 'flex',
                    gap: 8,
                    alignItems: 'baseline',
                    color: 'var(--ink-3)',
                    fontSize: 11,
                  }}
                >
                  <ControlPill status={exception.severity}>
                    {exception.severity}
                  </ControlPill>
                  <span>
                    {exception.summary}
                    {exception.count == null
                      ? ''
                      : ` · ${count(exception.count)}`}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--ink-4)', fontSize: 11, marginTop: 3 }}>
              The latest snapshot contains no critical or warning conditions.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
