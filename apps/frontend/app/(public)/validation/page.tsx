import type { Metadata } from 'next';
import Link from 'next/link';
import styles from './page.module.css';
import { ValidationPublication } from '@/components/ValidationPublication';
import { controlExceptionExplanation } from '@/lib/publicationPresentation';
import { requirePublicJson } from '@/lib/researchSnapshot.server';

export const metadata: Metadata = {
  title: 'Research Validation',
  description:
    'Audit Quantiv model performance, calibration, production controls, data quality, and evidence lineage.',
};

type Status =
  | 'passed'
  | 'advisory'
  | 'degraded'
  | 'failed'
  | 'warning'
  | 'unavailable'
  | string;

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
    quote_quality_errors?: string[];
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

const validation = requirePublicJson<ValidationArtifact>(
  'evidence',
  'model-validation.json',
);
const control = requirePublicJson<ControlPlane>('control-plane.json');
const forecast = requirePublicJson<ForecastEvidence>('evidence', 'forecast.json');

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
  return clean.length > 18
    ? `${clean.slice(0, 10)}…${clean.slice(-8)}`
    : clean;
}

function tone(status: Status | boolean): { label: string; color: string } {
  if (
    status === true ||
    status === 'passed' ||
    status === 'verified' ||
    status === 'enforced'
  ) {
    return {
      label: status === true ? 'Eligible' : String(status),
      color: 'var(--up)',
    };
  }
  if (status === false || status === 'failed' || status === 'critical') {
    return {
      label: status === false ? 'Blocked' : String(status),
      color: 'var(--down)',
    };
  }
  if (
    status === 'advisory' ||
    status === 'degraded' ||
    status === 'warning'
  ) {
    return {
      label: status === 'warning' ? 'warning' : 'advisory',
      color: 'var(--flag)',
    };
  }
  return {
    label: String(status || 'unavailable'),
    color: 'var(--ink-3)',
  };
}

function StatusPill({ status }: { status: Status | boolean }) {
  const value = tone(status);
  return (
    <span className={styles.statusPill} style={{ color: value.color }}>
      <span
        aria-hidden
        className={styles.statusDot}
        style={{ background: value.color }}
      />
      {value.label}
    </span>
  );
}

function SectionTitle({
  kicker,
  children,
  description,
}: {
  kicker: string;
  children: React.ReactNode;
  description?: string;
}) {
  return (
    <div className={styles.sectionHeading}>
      <div className={styles.kicker}>{kicker}</div>
      <h2>{children}</h2>
      {description ? <p>{description}</p> : null}
    </div>
  );
}

function CalibrationMeter({
  label,
  nominal,
  observed,
}: {
  label: string;
  nominal: number;
  observed: number | null | undefined;
}) {
  const observedPct =
    observed == null || !Number.isFinite(observed)
      ? null
      : Math.min(1, Math.max(0, observed));

  return (
    <div className={styles.calibrationMeter}>
      <div className={styles.calibrationTopline}>
        <span>{label}</span>
        <strong className="mono tnum">{pct(observed)}</strong>
      </div>
      <div className={styles.meterTrack} aria-hidden>
        <span
          className={styles.targetMark}
          style={{ left: `${nominal * 100}%` }}
        />
        {observedPct != null ? (
          <span
            className={styles.observedMark}
            style={{ left: `${observedPct * 100}%` }}
          />
        ) : null}
      </div>
      <div className={styles.meterCaption}>
        Target <span className="mono tnum">{pct(nominal)}</span>
      </div>
    </div>
  );
}

function MiniFact({
  label,
  value,
  toneColor,
}: {
  label: string;
  value: React.ReactNode;
  toneColor?: string;
}) {
  return (
    <div className={styles.miniFact}>
      <span>{label}</span>
      <strong
        className="mono tnum"
        style={toneColor ? { color: toneColor } : undefined}
      >
        {value}
      </strong>
    </div>
  );
}

export default function ValidationPage() {
  const sourceIsFallback = validation.model_source.kind !== 'signed_champion';
  const modelBundle = forecast.artifact_bundles.find(
    (item) => item.name === 'model_bundle',
  );
  const forecastBundle = forecast.artifact_bundles.find(
    (item) => item.name === 'forecast_snapshot',
  );
  const weighted = validation.summary.weighted_coverage;
  const improvement = validation.summary.weighted_relative_mae_improvement;
  const modelMae = validation.summary.weighted_model_mae;
  const baselineMae = validation.summary.weighted_straddle_mae;

  return (
    <main className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.kicker}>Model · data · evidence</div>
        <h1 className="qv-m-h1">Research validation</h1>
        <p className={styles.heroCopy}>
          See whether Quantiv forecasts beat the market baseline, how well the
          uncertainty estimates are calibrated, and whether the latest research
          state is publishable.
        </p>
        <div className={styles.heroMeta}>
          <span>Assessment</span>
          <strong className="mono">{dateLabel(control.generated_at)} ET</strong>
        </div>
      </header>

      {sourceIsFallback ? (
        <div role="note" className={styles.previewNote}>
          <div>
            <strong>Preview model source</strong>
            <span>
              This checkout is using fallback metadata rather than the signed
              production champion.
            </span>
          </div>
          <StatusPill status="advisory" />
        </div>
      ) : null}

      <section className={styles.section} aria-labelledby="model-performance">
        <SectionTitle
          kicker="Performance"
          description="The same holdout observations are scored by both Quantiv and the market-implied straddle baseline."
        >
          <span id="model-performance">Does the model add information?</span>
        </SectionTitle>

        <div className={styles.performanceHero}>
          <div className={styles.performanceLead}>
            <span className={styles.eyebrow}>Weighted error reduction</span>
            <div className={styles.heroNumber}>{pct(improvement, 1)}</div>
            <p>
              Lower mean absolute error than the straddle baseline across the
              supported research horizons.
            </p>
            <div className={styles.rangeLine}>
              Every horizon improves · {pct(validation.summary.min_relative_mae_improvement)}
              –{pct(validation.summary.max_relative_mae_improvement)}
            </div>
          </div>

          <div className={styles.errorCompare} aria-label="Model versus straddle error">
            <div className={styles.errorRow}>
              <div>
                <span>Quantiv model</span>
                <strong className="mono tnum">{pct(modelMae, 2)}</strong>
              </div>
              <div className={styles.errorTrack}>
                <span
                  className={styles.errorFillModel}
                  style={{
                    width: `${Math.min(
                      100,
                      Math.max(
                        8,
                        baselineMae && modelMae
                          ? (modelMae / baselineMae) * 100
                          : 0,
                      ),
                    )}%`,
                  }}
                />
              </div>
            </div>
            <div className={styles.errorRow}>
              <div>
                <span>Market straddle</span>
                <strong className="mono tnum">{pct(baselineMae, 2)}</strong>
              </div>
              <div className={styles.errorTrack}>
                <span className={styles.errorFillBaseline} />
              </div>
            </div>
            <div className={styles.observationCount}>
              <strong className="mono tnum">
                {count(validation.summary.validation_row_observations)}
              </strong>
              <span>holdout row-observations across {validation.horizons.length} horizon models</span>
            </div>
          </div>
        </div>

        <details className={styles.detailPanel}>
          <summary>Performance by horizon</summary>
          <div className={styles.tableWrap}>
            <table>
              <thead>
                <tr>
                  {[
                    'Horizon',
                    'Rows',
                    'Model MAE',
                    'Straddle MAE',
                    'Improvement',
                    '50% interval',
                    '80% interval',
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {validation.horizons.map((row) => (
                  <tr key={row.horizon_days}>
                    <td>T-{row.horizon_days}</td>
                    <td className="mono tnum">{count(row.n_validation)}</td>
                    <td className="mono tnum">{pct(row.model_mae, 2)}</td>
                    <td className="mono tnum">
                      {pct(row.straddle_baseline_mae, 2)}
                    </td>
                    <td className="mono tnum" style={{ color: 'var(--up)' }}>
                      {pct(row.relative_mae_improvement, 1)}
                    </td>
                    <td className="mono tnum">{pct(row.coverage.interval_50, 1)}</td>
                    <td className="mono tnum">{pct(row.coverage.interval_80, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.detailNote}>
            MAE is absolute stock-move error. This is predictive validation,
            not a trading-P&amp;L claim.
          </p>
        </details>
      </section>

      <section className={styles.section} aria-labelledby="calibration">
        <SectionTitle
          kicker="Uncertainty"
          description="Observed quantiles should land close to their nominal targets. The marker shows observed frequency; the thin line is the target."
        >
          <span id="calibration">Calibration</span>
        </SectionTitle>

        <div className={styles.calibrationGrid}>
          <CalibrationMeter label="P10" nominal={0.1} observed={weighted.p10} />
          <CalibrationMeter label="P50" nominal={0.5} observed={weighted.p50} />
          <CalibrationMeter label="P90" nominal={0.9} observed={weighted.p90} />
          <CalibrationMeter
            label="80% interval"
            nominal={0.8}
            observed={weighted.interval_80}
          />
        </div>

        <div className={styles.secondaryCalibration}>
          <span>Additional checks</span>
          <div>
            <MiniFact label="P25 observed" value={pct(weighted.p25)} />
            <MiniFact label="P75 observed" value={pct(weighted.p75)} />
            <MiniFact label="50% interval" value={pct(weighted.interval_50)} />
          </div>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="publication-status">
        <SectionTitle
          kicker="Publication"
          description="The last validated release and the ability to publish the next one are different states. This is the only place both are summarized."
        >
          <span id="publication-status">Can new research publish?</span>
        </SectionTitle>

        <ValidationPublication control={control} forecast={forecast} />

        <div className={styles.evidenceStrip} aria-label="Published forecast evidence counts">
          <MiniFact
            label="Controls checked"
            value={count(forecast.controls.evaluated)}
          />
          <MiniFact
            label="Exceptions"
            value={count(forecast.controls.exceptions)}
            toneColor={
              forecast.controls.exceptions > 0 ? 'var(--down)' : 'var(--up)'
            }
          />
          <MiniFact label="Forecast rows" value={count(forecast.coverage.rows)} />
          <MiniFact label="Events" value={count(forecast.coverage.events)} />
        </div>

        <div className={styles.operationsGrid}>
          <article className={styles.operationCard}>
            <div className={styles.cardHeader}>
              <div>
                <span className={styles.eyebrow}>Model safeguards</span>
                <h3>Serving model</h3>
              </div>
              <StatusPill status={control.model.status} />
            </div>
            <div className={styles.factGrid}>
              <MiniFact
                label="Champion"
                value={control.model.champion_active ? 'active' : 'inactive'}
                toneColor={
                  control.model.champion_active ? 'var(--up)' : 'var(--down)'
                }
              />
              <MiniFact
                label="Drift"
                value={tone(control.model.drift_status).label}
                toneColor={tone(control.model.drift_status).color}
              />
              <MiniFact
                label="Fallback"
                value={
                  control.model.fallback_bundle_available
                    ? 'available'
                    : 'unavailable'
                }
              />
              <MiniFact
                label="Shadow roles"
                value={
                  control.model.shadow_roles.length
                    ? control.model.shadow_roles.join(', ')
                    : 'none'
                }
              />
            </div>
          </article>

          <article className={styles.operationCard}>
            <div className={styles.cardHeader}>
              <div>
                <span className={styles.eyebrow}>Data safeguards</span>
                <h3>Decision universe</h3>
              </div>
              <StatusPill status={control.data.status} />
            </div>
            <div className={styles.factGrid}>
              <MiniFact
                label="Event coverage"
                value={pct(control.data.event_coverage_pct)}
              />
              <MiniFact
                label="Eligible contracts"
                value={`${count(control.data.eligible_contracts)} / ${count(
                  control.data.contracts,
                )}`}
              />
              <MiniFact
                label="Quarantine"
                value={count(control.data.quarantine_records)}
              />
              <MiniFact
                label="Duplicates"
                value={count(control.data.duplicate_rows)}
              />
            </div>
          </article>
        </div>

        {control.exceptions.length > 0 ? (
          <div className={styles.exceptionPanel}>
            <div className={styles.exceptionHeader}>
              <div>
                <span className={styles.eyebrow}>Needs attention</span>
                <h3>Why publication is constrained</h3>
              </div>
              <span className="mono tnum">{control.exceptions.length}</span>
            </div>
            <div className={styles.exceptionList}>
              {control.exceptions.map((item) => (
                <div key={item.code} className={styles.exception}>
                  <StatusPill status={item.severity} />
                  <span>
                    {controlExceptionExplanation(item, control.data)}
                  </span>
                  {item.code !== 'option_quote_quality_below_limit' &&
                  item.count != null ? (
                    <span className="mono tnum">{count(item.count)}</span>
                  ) : null}
                </div>
              ))}
            </div>
            <p>
              These gates affect the next research release. They do not rewrite a
              previously validated forecast receipt.
            </p>
          </div>
        ) : null}
      </section>

      <section className={styles.section} aria-labelledby="method">
        <SectionTitle
          kicker="Method"
          description="The validation process can be reduced to three checks. Technical controls remain available in the audit trail below."
        >
          <span id="method">How the result is earned</span>
        </SectionTitle>

        <div className={styles.methodGrid}>
          <article>
            <span className={styles.stepNumber}>01</span>
            <h3>Separate past from future</h3>
            <p>
              {validation.validation_protocol.walk_forward.expanding_windows}{' '}
              expanding walk-forward windows with a{' '}
              {validation.validation_protocol.walk_forward.validation_window_days}
              d validation window and{' '}
              {validation.validation_protocol.walk_forward.purge_days}d purge.
            </p>
          </article>
          <article>
            <span className={styles.stepNumber}>02</span>
            <h3>Beat the same market baseline</h3>
            <p>
              Every candidate is compared on the same observations against the
              straddle baseline, while point error and interval calibration are
              checked together.
            </p>
          </article>
          <article>
            <span className={styles.stepNumber}>03</span>
            <h3>Verify before publication</h3>
            <p>
              Bundle identity, feature schema, shadow scoring, drift controls
              and rollback evidence all stay tied to the model that produced
              the stored forecasts.
            </p>
          </article>
        </div>
      </section>

      <details className={styles.auditPanel}>
        <summary>
          <span>
            <span className={styles.kicker}>Technical audit trail</span>
            <strong>Evidence behind this page</strong>
          </span>
          <span aria-hidden>+</span>
        </summary>

        <div className={styles.auditBody}>
          <div className={styles.lineageList}>
            {[
              [
                'Forecast receipt',
                shortHash(forecast.receipt_id),
                forecast.receipt_file,
              ],
              [
                'Model artifact',
                shortHash(
                  validation.model_source.artifact_sha256 ?? modelBundle?.sha256,
                ),
                modelBundle?.producer ?? 'model trainer',
              ],
              [
                'Forecast artifact',
                shortHash(forecastBundle?.sha256),
                forecastBundle?.producer ?? 'daily scoring',
              ],
              [
                'Active bundle',
                shortHash(validation.model_source.bundle_id),
                validation.model_source.kind === 'signed_champion'
                  ? 'signed champion'
                  : 'preview fallback',
              ],
              [
                'Control snapshot',
                dateLabel(control.generated_at),
                `${control.data.decision_scope ?? 'end_of_day_research'} · publication ${
                  control.publication_eligible ? 'eligible' : 'blocked'
                }`,
              ],
            ].map(([label, value, detail]) => (
              <div key={label} className={styles.lineage}>
                <span>{label}</span>
                <strong className="mono">{value}</strong>
                <span>{detail}</span>
              </div>
            ))}
          </div>

          <div className={styles.scopeCallout}>
            <div>
              <span className={styles.eyebrow}>Decision scope</span>
              <p>
                Quantiv outputs are end-of-day research evidence. Live stock
                prices may update spot-derived inputs, but options, IV, Greeks
                and other snapshot features remain frozen. The validation page
                does not present executable option quotes or live-trading
                signals.
              </p>
            </div>
            <Link href="/about">Read methodology →</Link>
          </div>
        </div>
      </details>
    </main>
  );
}
