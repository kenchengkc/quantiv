import { BrainCircuit, CalendarDays, Database, ShieldCheck } from 'lucide-react';

type ForecastEvidence = {
  validated_at: string;
  quality: {
    status: string;
    issue_count: number;
  };
  coverage: {
    rows: number;
    symbols: number;
    events: number;
    horizons: number[];
  };
  controls: {
    evaluated: number;
    exceptions: number;
  };
};

type SnapshotStageProps = {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
};

function isForecastEvidence(value: unknown): value is ForecastEvidence {
  if (!value || typeof value !== 'object') return false;
  const evidence = value as Partial<ForecastEvidence>;
  return (
    typeof evidence.validated_at === 'string' &&
    typeof evidence.quality?.status === 'string' &&
    typeof evidence.quality?.issue_count === 'number' &&
    typeof evidence.controls?.evaluated === 'number' &&
    typeof evidence.controls?.exceptions === 'number'
  );
}

function shortDate(value?: string | null): string {
  if (!value) return 'Not reported';
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

function timingLabel(value?: string | null): string {
  const timing = (value ?? '').toLowerCase();
  if (['amc', 'after_market_close', 'after_close'].includes(timing)) {
    return 'after close';
  }
  if (['bmo', 'before_market_open', 'before_open'].includes(timing)) {
    return 'before open';
  }
  return 'time unconfirmed';
}

function SnapshotStage({ icon, label, value, detail }: SnapshotStageProps) {
  return (
    <div
      style={{
        minWidth: 0,
        display: 'grid',
        gridTemplateColumns: '24px minmax(0, 1fr)',
        gap: 9,
        alignItems: 'start',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 24,
          height: 24,
          borderRadius: 7,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--brand-blue-1)',
          background:
            'color-mix(in oklab, var(--brand-blue-1) 10%, transparent)',
        }}
      >
        {icon}
      </span>
      <span style={{ minWidth: 0 }}>
        <span
          style={{
            display: 'block',
            color: 'var(--ink-4)',
            fontSize: 9,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </span>
        <strong
          className="mono tnum"
          style={{
            display: 'block',
            marginTop: 3,
            color: 'var(--ink)',
            fontSize: 12,
            fontWeight: 650,
          }}
        >
          {value}
        </strong>
        <small
          style={{
            display: 'block',
            marginTop: 2,
            color: 'var(--ink-4)',
            fontSize: 10,
            lineHeight: 1.35,
          }}
        >
          {detail}
        </small>
      </span>
    </div>
  );
}

export default function ResearchSnapshotRibbon({
  evidence: rawEvidence,
  optionsDate,
  earningsDate,
  earningsTiming,
  modelSnapshotDate,
  modelHorizon,
}: {
  evidence: unknown;
  optionsDate: string;
  earningsDate?: string | null;
  earningsTiming?: string | null;
  modelSnapshotDate?: string | null;
  modelHorizon?: number | null;
}) {
  const evidence = isForecastEvidence(rawEvidence) ? rawEvidence : null;
  const passed = evidence?.quality.status === 'passed';
  const statusLabel = evidence
    ? passed
      ? 'Validated'
      : 'Review required'
    : 'Validation unavailable';
  const modelLabel =
    modelHorizon != null && Number.isFinite(modelHorizon)
      ? `T-${modelHorizon} model`
      : 'Model horizon not reported';
  const controlsValue = evidence
    ? `${evidence.controls.evaluated} controls`
    : 'Controls not reported';
  const controlsDetail = evidence
    ? `${evidence.controls.exceptions} exceptions · checked ${shortDate(evidence.validated_at)}`
    : 'No validation manifest was loaded';

  return (
    <section
      aria-label="Research snapshot and forecast validation"
      style={{
        marginTop: 14,
        padding: '11px 14px 12px',
        border: '1px solid var(--line)',
        borderRadius: 10,
        background: 'color-mix(in oklab, var(--bg-2) 58%, transparent)',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 10,
          marginBottom: 11,
        }}
      >
        <div
          style={{
            color: 'var(--ink-3)',
            fontSize: 10,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
          }}
        >
          Research snapshot
        </div>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
          <span
            style={{
              borderRadius: 999,
              padding: '2px 7px',
              color: passed ? 'var(--up)' : 'var(--flag)',
              background: passed
                ? 'color-mix(in oklab, var(--up) 9%, transparent)'
                : 'color-mix(in oklab, var(--flag) 9%, transparent)',
              fontSize: 9,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            {statusLabel}
          </span>
          <span
            style={{
              borderRadius: 999,
              padding: '2px 7px',
              color: 'var(--ink-3)',
              border: '1px solid var(--line)',
              fontSize: 9,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            End-of-day research
          </span>
        </div>
      </div>

      <div
        className="qv-m-2col"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 16,
        }}
      >
        <SnapshotStage
          icon={<Database size={13} />}
          label="Options snapshot"
          value={shortDate(optionsDate)}
          detail="Frozen chain inputs"
        />
        <SnapshotStage
          icon={<CalendarDays size={13} />}
          label="Earnings event"
          value={shortDate(earningsDate)}
          detail={timingLabel(earningsTiming)}
        />
        <SnapshotStage
          icon={<BrainCircuit size={13} />}
          label="Forecast model"
          value={modelLabel}
          detail={`Inputs from ${shortDate(modelSnapshotDate ?? optionsDate)}`}
        />
        <SnapshotStage
          icon={<ShieldCheck size={13} />}
          label="Forecast checks"
          value={controlsValue}
          detail={controlsDetail}
        />
      </div>
    </section>
  );
}
