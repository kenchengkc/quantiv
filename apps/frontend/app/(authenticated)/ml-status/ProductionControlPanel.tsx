'use client';

import { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  GitCompareArrows,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react';

type ControlStatus = 'passed' | 'degraded' | 'failed' | 'unavailable';

type Exception = {
  code: string;
  severity: 'warning' | 'critical';
  summary: string;
  count?: number;
};

type ControlSnapshot = {
  generated_at: string;
  status: ControlStatus;
  decision_safe: boolean;
  data: {
    status: ControlStatus;
    source_date: string | null;
    expected_source_date: string | null;
    source_session_lag: number | null;
    event_coverage_pct: number | null;
    expected_events: number | null;
    covered_events: number | null;
    missing_events: number | null;
    contract_rejection_rate: number | null;
    pair_rejection_rate: number | null;
    contracts: number | null;
    eligible_contracts: number | null;
    live_trading_eligible: boolean;
    decision_scope: string | null;
    quarantine_records: number | null;
    quarantine_status: string;
    replay_status: string;
    corporate_action_status: string;
    corporate_action_rows: number;
    duplicate_rows: number;
  };
  model: {
    status: ControlStatus;
    monitored_at: string | null;
    snapshot_date: string | null;
    champion_active: boolean;
    challenger_present: boolean;
    shadow_roles: string[];
    drift_status: string;
    critical_features: number | null;
    hard_missing_features: number | null;
    warning_features: number;
    rollback_ready: boolean;
    outcome_status: string;
    rollback_recorded: boolean;
  };
  exceptions: Exception[];
};

const EMPTY: ControlSnapshot = {
  generated_at: '',
  status: 'unavailable',
  decision_safe: false,
  data: {
    status: 'unavailable',
    source_date: null,
    expected_source_date: null,
    source_session_lag: null,
    event_coverage_pct: null,
    expected_events: null,
    covered_events: null,
    missing_events: null,
    contract_rejection_rate: null,
    pair_rejection_rate: null,
    contracts: null,
    eligible_contracts: null,
    live_trading_eligible: false,
    decision_scope: null,
    quarantine_records: null,
    quarantine_status: 'unavailable',
    replay_status: 'unavailable',
    corporate_action_status: 'unavailable',
    corporate_action_rows: 0,
    duplicate_rows: 0,
  },
  model: {
    status: 'unavailable',
    monitored_at: null,
    snapshot_date: null,
    champion_active: false,
    challenger_present: false,
    shadow_roles: [],
    drift_status: 'unavailable',
    critical_features: null,
    hard_missing_features: null,
    warning_features: 0,
    rollback_ready: false,
    outcome_status: 'unavailable',
    rollback_recorded: false,
  },
  exceptions: [],
};

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

export default function ProductionControlPanel() {
  const [snapshot, setSnapshot] = useState<ControlSnapshot>(EMPTY);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    fetch('/control-plane.json', {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then((response) =>
        response.ok
          ? response.json()
          : Promise.reject(new Error('control snapshot unavailable')),
      )
      .then((payload: ControlSnapshot) => setSnapshot(payload))
      .catch(() => undefined)
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const { data, model } = snapshot;
  const overallLabel = snapshot.decision_safe
    ? 'Decision-safe'
    : snapshot.status === 'unavailable'
      ? 'Controls not reported'
      : 'Publication blocked';
  const overallColor = snapshot.decision_safe
    ? 'var(--up)'
    : snapshot.status === 'unavailable'
      ? 'var(--ink-3)'
      : 'var(--down)';
  const snapshotUnavailable = !loading && snapshot.status === 'unavailable';

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
            {snapshot.decision_safe ? (
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
            One exception-first view of data readiness and model safety.
            Detailed receipts stay in the pipeline artifacts.
          </p>
        </div>
        <ControlPill status={snapshot.status}>
          {loading ? 'Loading' : statusLabel(snapshot.status)}
        </ControlPill>
      </div>

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
            label="Quote rejection"
            value={percent(data.contract_rejection_rate)}
            detail={`${percent(data.pair_rejection_rate)} same-strike pairs rejected`}
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
            detail={`${count(model.critical_features)} critical · ${count(model.warning_features)} warnings`}
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
          <div
            style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 11 }}
          >
            <ControlPill
              status={model.rollback_ready ? 'passed' : 'unavailable'}
            >
              <RotateCcw size={11} style={{ marginRight: 4 }} />
              Rollback {model.rollback_ready ? 'ready' : 'not ready'}
            </ControlPill>
            <ControlPill status={model.outcome_status}>
              Outcomes {statusLabel(model.outcome_status)}
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
              The next successful data refresh will publish the first production-control snapshot.
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
