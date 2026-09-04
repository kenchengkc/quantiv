import type { Metadata } from 'next';
import Link from 'next/link';
import controlPlaneJson from '../../../public/control-plane.json';
import forecastEvidenceJson from '../../../public/evidence/forecast.json';
import modelValidationJson from '../../../public/evidence/model-validation.json';

export const metadata: Metadata = {
  title: 'Research Validation',
  description:
    'Audit Quantiv model performance, calibration, production controls, data quality, and evidence lineage.',
};

type Status = 'passed' | 'degraded' | 'failed' | 'warning' | 'unavailable' | string;

type HorizonValidation = {
  horizon_days: number;
  n_train: number | null;
  n_validation: number;
  model_mae: number;
  straddle_baseline_mae: number;
  relative_mae_improvement: number;
  model_rmse: number | null;
  model_r2: number | null;
  coverage: {
    p10: number | null;
    p25: number | null;
    p50: number | null;
    p75: number | null;
    p90: number | null;
    interval_50: number | null;
    interval_80: number | null;
  };
  feature_count: number;
  model_version: string | null;
  trained_at: string | null;
};

type ValidationArtifact = {
  schema: string;
  generated_at: string;
  model_source: {
    kind: 'signed_champion' | 'baked_fallback' | string;
    bundle_id: string | null;
    artifact_sha256: string | null;
  };
  summary: {
    validation_row_observations: number;
    weighted_model_mae: number | null;
    weighted_straddle_mae: number | null;
    weighted_relative_mae_improvement: number | null;
    min_relative_mae_improvement: number | null;
    max_relative_mae_improvement: number | null;
    weighted_coverage: Record<string, number | null>;
  };
  horizons: HorizonValidation[];
  validation_protocol: {
    target: string;
    baseline: string;
    chronological_holdout: boolean;
    walk_forward: {
      expanding_windows: number;
      validation_window_days: number;
      purge_days: number;
    };
    promotion_controls: string[];
    decision_scope: string;
    live_trading_eligible: boolean;
  };
  current_evidence: {
    forecast_receipt_id: string | null;
    forecast_validated_at: string | null;
    forecast_quality: Status;
    forecast_control_exceptions: number | null;
    forecast_rows: number | null;
    forecast_events: number | null;
    control_plane_status: Status;
    publication_eligible: boolean | null;
    data_status: Status;
    model_status: Status;
    drift_status: Status;
  };
};

type ControlPlane = {
  status: Status;
  publication_eligible: boolean;
  generated_at: string;
  data: {
    status: Status;
    source_date: string | null;
    expected_source_date: string | null;
    source_session_lag: number | null;
    event_coverage_pct: number | null;
    expected_events: number | null;
    covered_events: number | null;
    missing_events: number | null;
    contract_rejection_rate: number | null;
    pair_rejection_rate: number | null;
    decision_group_rejection_rate: number | null;
    decision_groups: number | null;
    eligible_decision_groups: number | null;
    contracts: number | null;
    eligible_contracts: number | null;
    live_trading_eligible: boolean;
    decision_scope: string | null;
    quarantine_records: number | null;
    quarantine_status: Status;
    replay_status: Status;
    corporate_action_status: Status;
    corporate_action_rows: number | null;
    duplicate_rows: number | null;
  };
  model: {
    status: Status;
    drift_status: Status;
    champion_active: boolean;
    challenger_present: boolean;
    fallback_bundle_available: boolean;
    critical_features: number | null;
    warning_features: number | null;
    hard_missing_features: number | null;
    shadow_roles: string[];
  };
  release: Record<string, Status>;
  exceptions: Array<{
    code: string;
    severity: string;
    summary: string;
    count?: number;
  }>;
};

type ForecastEvidence = {
  receipt_id: string;
  receipt_file: string;
  validated_at: string;
  quality: { status: Status; issue_count: number; issue_codes: string[] };
  coverage: { rows: number; symbols: number; events: number; horizons: number[] };
  controls: { evaluated: number; exceptions: number };
  artifact_bundles: Array<{
    name: string;
    producer: string;
    member_count: number;
    bytes: number;
    sha256: string;
  }>;
};

const validation = modelValidationJson as unknown as ValidationArtifact;
const control = controlPlaneJson as unknown as ControlPlane;
const forecast = forecastEvidenceJson as unknown as ForecastEvidence;

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

function count(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return Math.round(value).toLocaleString('en-US');
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return 'Unavailable';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/New_York',
  });
}

function shortHash(value: string | null | undefined): string {
  if (!value) return 'Unavailable';
  const clean = value.replace(/^sha256:/, '');
  return clean.length > 18 ? `${clean.slice(0, 10)}…${clean.slice(-8)}` : clean;
}

function tone(status: Status | boolean): { label: string; color: string } {
  if (status === true || status === 'passed' || status === 'verified' || status === 'enforced') {
    return { label: status === true ? 'Eligible' : String(status), color: 'var(--up)' };
  }
  if (status === false || status === 'failed' || status === 'critical') {
    return { label: status === false ? 'Blocked' : String(status), color: 'var(--down)' };
  }
  if (status === 'degraded' || status === 'warning') {
    return { label: String(status), color: 'var(--warn)' };
  }
  return { label: String(status || 'unavailable'), color: 'var(--ink-3)' };
}

function StatusPill({ status }: { status: Status | boolean }) {
  const value = tone(status);
  return (
    <span
      className="mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 8px',
        borderRadius: 999,
        border: '1px solid var(--line)',
        fontSize: 10,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color: value.color,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        aria-hidden
        style={{ width: 6, height: 6, borderRadius: 999, background: value.color }}
      />
      {value.label}
    </span>
  );
}

function SectionTitle({ kicker, children }: { kicker: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div
        className="mono"
        style={{
          fontSize: 10,
          color: 'var(--ink-3)',
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          marginBottom: 8,
        }}
      >
        {kicker}
      </div>
      <h2 style={{ margin: 0, fontSize: 32, fontWeight: 600, letterSpacing: '-0.025em' }}>
        {children}
      </h2>
    </div>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div
      style={{
        minHeight: 148,
        border: '1px solid var(--line)',
        borderRadius: 14,
        background: 'var(--bg-2)',
        padding: 18,
      }}
    >
      <div
        className="mono"
        style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.12em', textTransform: 'uppercase' }}
      >
        {label}
      </div>
      <div className="tnum" style={{ marginTop: 18, fontSize: 32, fontWeight: 600, letterSpacing: '-0.025em' }}>
        {value}
      </div>
      <div style={{ marginTop: 10, fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>{detail}</div>
    </div>
  );
}

export default function ValidationPage() {
  const sourceIsFallback = validation.model_source.kind !== 'signed_champion';
  const modelBundle = forecast.artifact_bundles.find((item) => item.name === 'model_bundle');
  const forecastBundle = forecast.artifact_bundles.find((item) => item.name === 'forecast_snapshot');
  const weighted = validation.summary.weighted_coverage;

  return (
    <main className="qv-m-pad" style={{ maxWidth: 1180, margin: '0 auto', padding: '0 28px 84px' }}>
      <header style={{ padding: '32px 0 24px', borderBottom: '1px solid var(--line)' }}>
        <div
          className="mono"
          style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.18em', textTransform: 'uppercase' }}
        >
          Model · data · evidence
        </div>
        <h1
          className="qv-m-h1"
          style={{ margin: '18px 0 0', fontSize: 64, lineHeight: 0.96, fontWeight: 800, letterSpacing: '-0.035em' }}
        >
          Research validation
        </h1>
        <p style={{ margin: '22px 0 0', maxWidth: 760, color: 'var(--ink-2)', fontSize: 16, lineHeight: 1.65 }}>
          The due-diligence view of Quantiv. This page exposes out-of-sample model performance against the market straddle baseline,
          distribution calibration, current publication controls, data-quality exceptions, and the evidence identifiers behind the live research surface.
        </p>
      </header>

      <section
        aria-label="Current research status"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 10,
          alignItems: 'center',
          padding: '16px 0',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <span style={{ fontSize: 12, color: 'var(--ink-3)', marginRight: 4 }}>Current publication</span>
        <StatusPill status={control.status} />
        <span style={{ fontSize: 12, color: 'var(--ink-3)', marginLeft: 8 }}>Publication gate</span>
        <StatusPill status={control.publication_eligible} />
        <span style={{ fontSize: 12, color: 'var(--ink-3)', marginLeft: 8 }}>Forecast receipt</span>
        <StatusPill status={forecast.quality.status} />
        <span className="mono" style={{ marginLeft: 'auto', color: 'var(--ink-4)', fontSize: 10 }}>
          Validated {dateLabel(forecast.validated_at)} ET
        </span>
      </section>

      {sourceIsFallback && (
        <div
          role="note"
          style={{
            marginTop: 18,
            border: '1px solid var(--line)',
            borderRadius: 12,
            padding: '12px 14px',
            color: 'var(--ink-2)',
            background: 'var(--bg-2)',
            fontSize: 12,
            lineHeight: 1.55,
          }}
        >
          <strong style={{ color: 'var(--ink)' }}>Preview model source.</strong> This committed artifact was generated from the checked-in fallback model metadata.
          The nightly publication job prefers the signed active champion bundle after R2 synchronization and rewrites this artifact automatically.
        </div>
      )}

      <section style={{ paddingTop: 42 }}>
        <SectionTitle kicker="Primary question">Does the model add information?</SectionTitle>
        <div
          className="qv-m-2col"
          style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14 }}
        >
          <MetricCard
            label="Model MAE"
            value={pct(validation.summary.weighted_model_mae, 2)}
            detail="Validation-weighted absolute error across the six horizon-specific models."
          />
          <MetricCard
            label="Straddle baseline MAE"
            value={pct(validation.summary.weighted_straddle_mae, 2)}
            detail="The same validation observations scored against market-implied straddle move."
          />
          <MetricCard
            label="Relative MAE improvement"
            value={pct(validation.summary.weighted_relative_mae_improvement, 1)}
            detail={`Every horizon improves on the baseline; range ${pct(validation.summary.min_relative_mae_improvement)}–${pct(validation.summary.max_relative_mae_improvement)}.`}
          />
          <MetricCard
            label="Validation row-observations"
            value={count(validation.summary.validation_row_observations)}
            detail="Sum of holdout rows across horizon models; not a unique-event count."
          />
        </div>
      </section>

      <section style={{ paddingTop: 52 }}>
        <SectionTitle kicker="Out of sample">Performance by research horizon</SectionTitle>
        <div style={{ overflowX: 'auto', border: '1px solid var(--line)', borderRadius: 14 }}>
          <table style={{ width: '100%', minWidth: 820, borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: 'var(--bg-2)' }}>
                {['Horizon', 'Validation rows', 'Model MAE', 'Straddle MAE', 'Improvement', '50% coverage', '80% coverage'].map((label) => (
                  <th
                    key={label}
                    className="mono"
                    style={{
                      textAlign: label === 'Horizon' ? 'left' : 'right',
                      padding: '13px 14px',
                      borderBottom: '1px solid var(--line)',
                      color: 'var(--ink-3)',
                      fontSize: 10,
                      letterSpacing: '0.1em',
                      textTransform: 'uppercase',
                    }}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {validation.horizons.map((row) => (
                <tr key={row.horizon_days}>
                  <td style={{ padding: '14px', borderBottom: '1px solid var(--line)', fontWeight: 600 }}>T-{row.horizon_days}</td>
                  <td className="mono tnum" style={{ padding: '14px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{count(row.n_validation)}</td>
                  <td className="mono tnum" style={{ padding: '14px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{pct(row.model_mae, 2)}</td>
                  <td className="mono tnum" style={{ padding: '14px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{pct(row.straddle_baseline_mae, 2)}</td>
                  <td className="mono tnum" style={{ padding: '14px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: 'var(--up)' }}>{pct(row.relative_mae_improvement, 1)}</td>
                  <td className="mono tnum" style={{ padding: '14px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{pct(row.coverage.interval_50, 1)}</td>
                  <td className="mono tnum" style={{ padding: '14px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{pct(row.coverage.interval_80, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p style={{ margin: '12px 2px 0', color: 'var(--ink-3)', fontSize: 11.5, lineHeight: 1.55 }}>
          MAE is expressed as absolute stock-move fraction. The comparison is paired on each horizon&apos;s validation rows; this table is predictive evidence, not a trading-P&amp;L claim.
        </p>
      </section>

      <section style={{ paddingTop: 52 }}>
        <SectionTitle kicker="Distribution quality">Calibration</SectionTitle>
        <div className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14 }}>
          <MetricCard label="P10 observed" value={pct(weighted.p10)} detail="Nominal target: 10%." />
          <MetricCard label="P50 observed" value={pct(weighted.p50)} detail="Nominal target: 50%." />
          <MetricCard label="P90 observed" value={pct(weighted.p90)} detail="Nominal target: 90%." />
          <MetricCard label="80% interval coverage" value={pct(weighted.interval_80)} detail="Nominal target: 80%." />
        </div>
        <div style={{ marginTop: 14, border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Weighted quantile calibration
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 10, marginTop: 16 }}>
            {[
              ['P10', 0.1, weighted.p10],
              ['P25', 0.25, weighted.p25],
              ['P50', 0.5, weighted.p50],
              ['P75', 0.75, weighted.p75],
              ['P90', 0.9, weighted.p90],
            ].map(([label, nominal, observed]) => (
              <div key={String(label)} style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11, color: 'var(--ink-3)' }}>
                  <span>{label}</span>
                  <span className="mono tnum">{pct(observed as number | null)}</span>
                </div>
                <div style={{ marginTop: 8, height: 5, borderRadius: 999, background: 'var(--bg-3)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${Math.min(100, Math.max(0, Number(observed) * 100))}%`, background: 'var(--accent)' }} />
                </div>
                <div className="mono" style={{ marginTop: 6, fontSize: 9.5, color: 'var(--ink-4)' }}>nominal {pct(nominal as number)}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section style={{ paddingTop: 52 }}>
        <SectionTitle kicker="Production evidence">Current research controls</SectionTitle>
        <div className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 14 }}>
          <div style={{ border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <h3 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Forecast publication</h3>
              <StatusPill status={forecast.quality.status} />
            </div>
            <dl style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '10px 18px', margin: '20px 0 0', fontSize: 12 }}>
              <dt style={{ color: 'var(--ink-3)' }}>Controls evaluated</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.controls.evaluated)}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Control exceptions</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.controls.exceptions)}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Forecast rows</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.coverage.rows)}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Upcoming events</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.coverage.events)}</dd>
            </dl>
          </div>

          <div style={{ border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <h3 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Model control plane</h3>
              <StatusPill status={control.model.status} />
            </div>
            <dl style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '10px 18px', margin: '20px 0 0', fontSize: 12 }}>
              <dt style={{ color: 'var(--ink-3)' }}>Champion active</dt><dd style={{ margin: 0 }}><StatusPill status={control.model.champion_active} /></dd>
              <dt style={{ color: 'var(--ink-3)' }}>Drift status</dt><dd style={{ margin: 0 }}><StatusPill status={control.model.drift_status} /></dd>
              <dt style={{ color: 'var(--ink-3)' }}>Fallback bundle</dt><dd className="mono" style={{ margin: 0 }}>{control.model.fallback_bundle_available ? 'available' : 'unavailable'}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Shadow roles</dt><dd className="mono" style={{ margin: 0 }}>{control.model.shadow_roles.length ? control.model.shadow_roles.join(', ') : 'none'}</dd>
            </dl>
          </div>
        </div>

        <div style={{ marginTop: 14, border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Data decision universe</h3>
            <StatusPill status={control.data.status} />
          </div>
          <div className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14, marginTop: 18 }}>
            <MetricCard label="Eligible contracts" value={`${count(control.data.eligible_contracts)} / ${count(control.data.contracts)}`} detail="Contracts surviving commercial quote-quality controls." />
            <MetricCard label="Event coverage" value={pct(control.data.event_coverage_pct)} detail={`${count(control.data.covered_events)} of ${count(control.data.expected_events)} in-universe upcoming events currently covered.`} />
            <MetricCard label="Quarantine records" value={count(control.data.quarantine_records)} detail={`Rejected evidence retained; quarantine ${control.data.quarantine_status}.`} />
            <MetricCard label="Duplicate rows" value={count(control.data.duplicate_rows)} detail={`Replay ${control.data.replay_status}; corporate actions ${control.data.corporate_action_status}.`} />
          </div>

          {control.exceptions.length > 0 && (
            <div style={{ marginTop: 18, borderTop: '1px solid var(--line)', paddingTop: 16 }}>
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>Current advisory exceptions</div>
              <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                {control.exceptions.map((item) => (
                  <div key={item.code} style={{ display: 'grid', gridTemplateColumns: '120px 1fr auto', gap: 12, alignItems: 'center', fontSize: 12 }}>
                    <StatusPill status={item.severity} />
                    <span style={{ color: 'var(--ink-2)' }}>{item.summary}</span>
                    <span className="mono tnum" style={{ color: 'var(--ink-3)' }}>{item.count != null ? count(item.count) : ''}</span>
                  </div>
                ))}
              </div>
              <p style={{ margin: '12px 0 0', color: 'var(--ink-3)', fontSize: 11.5, lineHeight: 1.55 }}>
                “Degraded” is intentionally distinct from failed: advisory coverage/drift warnings remain visible while critical exceptions fail publication closed.
              </p>
            </div>
          )}
        </div>
      </section>

      <section style={{ paddingTop: 52 }}>
        <SectionTitle kicker="Validation protocol">What has to pass</SectionTitle>
        <div className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 14 }}>
          {[
            ['Chronology', `${validation.validation_protocol.walk_forward.expanding_windows} expanding walk-forward windows · ${validation.validation_protocol.walk_forward.validation_window_days}d validation · ${validation.validation_protocol.walk_forward.purge_days}d purge`],
            ['Baseline', 'Candidate models must beat the same-observation market straddle baseline; promotion also compares candidate and champion on a common purged holdout.'],
            ['Distribution', 'Point error, quantile ordering, P10/P25/P50/P75/P90 behavior, 50%/80% coverage and interval quality are gated together.'],
            ['Shadow scoring', 'Upcoming events are scored by the candidate before control changes, surfacing material divergence ahead of promotion.'],
            ['Artifact integrity', 'Immutable model bundles carry exact feature schemas and content digests; serving activation is tied to the same bundle identity used for stored forecasts.'],
            ['Rollback', 'Realized monitoring retains champion/comparison evidence and can record a signed rollback when minimum common-outcome and deterioration thresholds are met.'],
          ].map(([title, body]) => (
            <article key={title} style={{ border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{title}</h3>
              <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.6 }}>{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section style={{ paddingTop: 52 }}>
        <SectionTitle kicker="Lineage">Evidence behind this page</SectionTitle>
        <div style={{ border: '1px solid var(--line)', borderRadius: 14, overflow: 'hidden' }}>
          {[
            ['Forecast receipt', shortHash(forecast.receipt_id), forecast.receipt_file],
            ['Model artifact', shortHash(validation.model_source.artifact_sha256 ?? modelBundle?.sha256), modelBundle?.producer ?? 'model trainer'],
            ['Forecast artifact', shortHash(forecastBundle?.sha256), forecastBundle?.producer ?? 'daily scoring'],
            ['Active bundle', shortHash(validation.model_source.bundle_id), validation.model_source.kind === 'signed_champion' ? 'signed champion' : 'preview fallback'],
            ['Control snapshot', dateLabel(control.generated_at), `${control.data.decision_scope ?? 'end_of_day_research'} · publication ${control.publication_eligible ? 'eligible' : 'blocked'}`],
          ].map(([label, value, detail], index, rows) => (
            <div
              key={label}
              style={{
                display: 'grid',
                gridTemplateColumns: '180px minmax(0, 1fr) minmax(220px, 0.8fr)',
                gap: 16,
                padding: '14px 16px',
                borderBottom: index < rows.length - 1 ? '1px solid var(--line)' : 'none',
                alignItems: 'center',
                fontSize: 12,
              }}
            >
              <span style={{ color: 'var(--ink-3)' }}>{label}</span>
              <span className="mono" style={{ color: 'var(--ink)' }}>{value}</span>
              <span style={{ color: 'var(--ink-3)' }}>{detail}</span>
            </div>
          ))}
        </div>
      </section>

      <section
        style={{
          marginTop: 52,
          borderTop: '1px solid var(--line)',
          borderBottom: '1px solid var(--line)',
          padding: '20px 0',
          display: 'flex',
          gap: 24,
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ maxWidth: 760 }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--warn)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>Decision scope</div>
          <p style={{ margin: '8px 0 0', color: 'var(--ink-2)', fontSize: 12.5, lineHeight: 1.65 }}>
            Quantiv model outputs are end-of-day research evidence. A latest stock quote may update spot-derived inputs, but options, IV, Greeks and other snapshot features remain frozen. These results are not presented as executable option quotes or live-trading signals.
          </p>
        </div>
        <Link href="/about" style={{ fontSize: 12, color: 'var(--accent)', whiteSpace: 'nowrap', marginTop: 18 }}>
          Read methodology →
        </Link>
      </section>
    </main>
  );
}
