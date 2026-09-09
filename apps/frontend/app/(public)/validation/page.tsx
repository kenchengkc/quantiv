import type { Metadata } from 'next';
import Link from 'next/link';
import styles from './page.module.css';
import { ValidationPublication } from '@/components/ValidationPublication';
import { controlExceptionExplanation, publishedForecastStatus } from '@/lib/publicationPresentation';
import { requirePublicJson } from '@/lib/researchSnapshot.server';

export const metadata: Metadata = {
  title: {
    absolute: 'Research Validation | Quantiv',
  },
};

export const dynamic = 'force-static';

type ValidationBundle = {
  schema: string;
  generated_at: string;
  model_source: {
    kind: string;
    bundle_id?: string;
    artifact_sha256?: string;
  };
  validation_protocol: {
    walk_forward: {
      expanding_windows: number;
      validation_window_days: number;
      purge_days: number;
    };
  };
  horizons: Array<{
    horizon_days: number;
    n_validation: number;
    model_mae: number | null;
    straddle_baseline_mae: number | null;
    relative_mae_improvement: number | null;
    coverage: {
      interval_50: number | null;
      interval_80: number | null;
    };
  }>;
  weighted_calibration: {
    p10: number | null;
    p25: number | null;
    p50: number | null;
    p75: number | null;
    p90: number | null;
    interval_80: number | null;
  };
  evidence?: {
    model_bundle?: { sha256?: string; producer?: string };
    forecast_bundle?: { sha256?: string; producer?: string };
  };
};

type ForecastEvidence = {
  receipt_id: string;
  receipt_file: string;
  validated_at: string;
  status: string;
  controls: { evaluated: number; exceptions: number };
  coverage: { rows: number; events: number };
};

type ControlPlane = {
  generated_at: string;
  status: string;
  publication_eligible: boolean;
  data: {
    status: string;
    contracts: number;
    eligible_contracts: number;
    event_coverage_pct: number;
    covered_events: number;
    expected_events: number;
    quarantine_records: number;
    quarantine_status: string;
    duplicate_rows: number;
    replay_status: string;
    corporate_action_status: string;
    decision_scope?: string;
    source_date?: string;
    expected_source_date?: string;
    source_session_lag?: number;
    quote_quality_errors?: string[];
  };
  model: {
    status: string;
    champion_active: boolean;
    drift_status: string;
    fallback_bundle_available: boolean;
    shadow_roles: string[];
  };
  exceptions: Array<{
    code: string;
    count?: number;
    severity: string;
    summary?: string;
  }>;
};

type StatusValue = string | boolean | null | undefined;

function pct(value: number | null | undefined, digits = 1) {
  return value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(digits)}%`;
}

function count(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('en-US');
}

function dateLabel(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Unavailable';
  return new Date(value).toLocaleString('en-US', {
    month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'America/New_York',
  });
}

function shortHash(value: string | null | undefined) {
  if (!value) return 'Unavailable';
  const normalized = value.startsWith('sha256:') ? value.slice(7) : value;
  return normalized.length > 18 ? `${normalized.slice(0, 10)}…${normalized.slice(-6)}` : normalized;
}

function statusLabel(status: StatusValue) {
  if (status === true) return 'eligible';
  if (status === false) return 'unavailable';
  return String(status ?? 'unknown').replaceAll('_', ' ');
}

function statusTone(status: StatusValue) {
  const value = statusLabel(status).toLowerCase();
  if (['passed', 'eligible', 'active', 'verified', 'enforced', 'available'].includes(value)) return 'var(--up)';
  if (['failed', 'critical', 'blocked'].includes(value)) return 'var(--down)';
  if (['warning', 'degraded', 'review required'].includes(value)) return 'var(--flag)';
  return 'var(--ink-3)';
}

function StatusPill({ status }: { status: StatusValue }) {
  return (
    <span
      className="mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        padding: '5px 10px',
        borderRadius: 999,
        border: '1px solid var(--line)',
        color: statusTone(status),
        fontSize: 9.5,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
      {statusLabel(status)}
    </span>
  );
}

function SectionTitle({ kicker, children }: { kicker: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8 }}>
        {kicker}
      </div>
      <h2 className="serif" style={{ margin: 0, fontSize: 30, lineHeight: 1.05, fontWeight: 700 }}>
        {children}
      </h2>
    </div>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div style={{ border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div className="serif tnum" style={{ marginTop: 14, fontSize: 42, lineHeight: 1, letterSpacing: '-0.03em' }}>
        {value}
      </div>
      <p style={{ margin: '14px 0 0', color: 'var(--ink-3)', fontSize: 12, lineHeight: 1.55 }}>{detail}</p>
    </div>
  );
}

export default function ValidationPage() {
  const validation = requirePublicJson<ValidationBundle>('evidence', 'model-validation.json');
  const forecast = requirePublicJson<ForecastEvidence>('evidence', 'forecast.json');
  const control = requirePublicJson<ControlPlane>('control-plane.json');
  const weighted = validation.weighted_calibration;
  const modelBundle = validation.evidence?.model_bundle;
  const forecastBundle = validation.evidence?.forecast_bundle;

  return (
    <main style={{ maxWidth: 1180, margin: '0 auto', padding: '62px 28px 90px' }}>
      <section>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.16em', textTransform: 'uppercase' }}>
          Research evidence
        </div>
        <h1 className="serif" style={{ margin: '14px 0 0', maxWidth: 900, fontSize: 58, lineHeight: 0.98, letterSpacing: '-0.045em' }}>
          Research validation
        </h1>
        <p style={{ margin: '18px 0 0', maxWidth: 760, color: 'var(--ink-2)', fontSize: 16, lineHeight: 1.65 }}>
          Out-of-sample model evidence, calibration, publication controls, and artifact lineage for Quantiv&apos;s end-of-day research outputs.
        </p>
      </section>

      <section style={{ paddingTop: 52 }}>
        <SectionTitle kicker="Paired benchmark">Does the model add information?</SectionTitle>
        <div className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14 }}>
          {validation.horizons.slice(0, 4).map((row) => (
            <MetricCard
              key={row.horizon_days}
              label={`T-${row.horizon_days}`}
              value={pct(row.relative_mae_improvement)}
              detail={`Relative MAE improvement vs the same-observation straddle baseline on ${count(row.n_validation)} held-out rows.`}
            />
          ))}
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
        <SectionTitle kicker="Production evidence">Latest assessed research controls</SectionTitle>
        <ValidationPublication control={control} forecast={forecast} />
        <p style={{ margin: '12px 2px 0', color: 'var(--ink-3)', fontSize: 11.5, lineHeight: 1.55 }}>
          The control cards below are a captured assessment from {dateLabel(control.generated_at)} ET. A failed or degraded card blocks a new research release; it does not retroactively invalidate the separately validated forecast evidence above.
        </p>
        <div className={styles.controlCards}>
          <div style={{ border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <h3 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Published forecast evidence</h3>
              <StatusPill status={publishedForecastStatus(forecast)} />
            </div>
            <dl style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '10px 18px', margin: '20px 0 0', fontSize: 12 }}>
              <dt style={{ color: 'var(--ink-3)' }}>Controls evaluated</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.controls.evaluated)}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Control exceptions</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.controls.exceptions)}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Forecast rows</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.coverage.rows)}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Events in this release</dt><dd className="mono tnum" style={{ margin: 0 }}>{count(forecast.coverage.events)}</dd>
            </dl>
          </div>

          <div style={{ border: '1px solid var(--line)', borderRadius: 14, padding: 18, background: 'var(--bg-2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <h3 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Model control plane · assessed</h3>
              <StatusPill status={control.model.status} />
            </div>
            <dl style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '10px 18px', margin: '20px 0 0', fontSize: 12 }}>
              <dt style={{ color: 'var(--ink-3)' }}>Champion active at assessment</dt><dd style={{ margin: 0 }}><StatusPill status={control.model.champion_active} /></dd>
              <dt style={{ color: 'var(--ink-3)' }}>Drift status at assessment</dt><dd style={{ margin: 0 }}><StatusPill status={control.model.drift_status} /></dd>
              <dt style={{ color: 'var(--ink-3)' }}>Fallback bundle at assessment</dt><dd className="mono" style={{ margin: 0 }}>{control.model.fallback_bundle_available ? 'available' : 'unavailable'}</dd>
              <dt style={{ color: 'var(--ink-3)' }}>Shadow roles</dt><dd className="mono" style={{ margin: 0 }}>{control.model.shadow_roles.length ? control.model.shadow_roles.join(', ') : 'none'}</dd>
            </dl>
          </div>
        </div>

        <div style={{ marginTop: 14, border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Data decision universe · assessed</h3>
            <StatusPill status={control.data.status} />
          </div>
          <div className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 14, marginTop: 18 }}>
            <MetricCard label="Eligible contracts" value={`${count(control.data.eligible_contracts)} / ${count(control.data.contracts)}`} detail="Contracts that survived quote-quality controls in this captured assessment." />
            <MetricCard label="Event coverage" value={pct(control.data.event_coverage_pct)} detail={`${count(control.data.covered_events)} of ${count(control.data.expected_events)} in-universe upcoming events were covered in this captured assessment.`} />
            <MetricCard label="Quarantine records" value={count(control.data.quarantine_records)} detail={`Rejected evidence retained in this assessment; quarantine ${control.data.quarantine_status}.`} />
            <MetricCard label="Duplicate rows" value={count(control.data.duplicate_rows)} detail={`Assessment replay ${control.data.replay_status}; corporate actions ${control.data.corporate_action_status}.`} />
          </div>

          {control.exceptions.length > 0 && (
            <div style={{ marginTop: 18, borderTop: '1px solid var(--line)', paddingTop: 16 }}>
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>Latest assessed control exceptions</div>
              <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                {control.exceptions.map((item) => (
                  <div key={item.code} className={styles.exception}>
                    <StatusPill status={item.severity} />
                    <span style={{ color: 'var(--ink-2)' }}>{controlExceptionExplanation(item, control.data)}</span>
                    <span className="mono tnum" style={{ color: 'var(--ink-3)' }}>{item.code !== 'option_quote_quality_below_limit' && item.count != null ? count(item.count) : ''}</span>
                  </div>
                ))}
              </div>
              <p style={{ margin: '12px 0 0', color: 'var(--ink-3)', fontSize: 11.5, lineHeight: 1.55 }}>
                These exceptions describe the assessment captured above, not live market state. They gate eligibility for new research; a retained forecast receipt can remain passed while stale or ineligible options evidence blocks the next release. No publication threshold is relaxed.
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
              className={styles.lineage}
              style={{
                borderBottom: index < rows.length - 1 ? '1px solid var(--line)' : 'none',
              }}
            >
              <span style={{ color: 'var(--ink-3)' }}>{label}</span>
              <span className="mono" style={{ overflowWrap: 'anywhere' }}>{value}</span>
              <span style={{ color: 'var(--ink-4)', overflowWrap: 'anywhere' }}>{detail}</span>
            </div>
          ))}
        </div>
        <p style={{ margin: '12px 2px 0', color: 'var(--ink-3)', fontSize: 11.5, lineHeight: 1.55 }}>
          Downloads and validation evidence are immutable research artifacts. Live quote APIs are intentionally outside this evidence surface.
        </p>
        <div style={{ marginTop: 18 }}>
          <Link href="/methodology" style={{ color: 'var(--accent)', fontSize: 12 }}>
            Read the methodology →
          </Link>
        </div>
      </section>
    </main>
  );
}
