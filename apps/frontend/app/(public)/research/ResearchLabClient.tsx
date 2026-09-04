'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Copy, Download, RotateCcw } from 'lucide-react';
import type { CohortEvent, CohortSummary } from '@/lib/researchCohort';

type CohortResponse = {
  schema: string;
  snapshot_id: string;
  source: {
    public_symbol_payloads: number;
    source_as_of_min: string | null;
    source_as_of_max: string | null;
    eligible_event_universe: number;
    forecast_receipt_id: string | null;
    forecast_quality: string | null;
    control_status: string | null;
    publication_eligible: boolean | null;
  };
  decision_scope: string;
  live_trading_eligible: boolean;
  live_quote_overlay_included: boolean;
  matching_count: number;
  returned_count: number;
  summary: CohortSummary;
  events: CohortEvent[];
};

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

function ratio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value.toFixed(2)}x`;
}

function sessionLabel(value: string): string {
  const v = value.toLowerCase();
  if (v.includes('before') || v === 'bmo') return 'BMO';
  if (v.includes('after') || v === 'amc') return 'AMC';
  return 'Other';
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 120 }}>
      <span
        className="mono"
        style={{ fontSize: 9.5, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink-4)' }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}

function controlStyle(): React.CSSProperties {
  return {
    height: 34,
    borderRadius: 8,
    border: '1px solid var(--line)',
    background: 'var(--bg-2)',
    color: 'var(--ink-2)',
    padding: '0 10px',
    fontSize: 12,
    outline: 'none',
  };
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        background: 'var(--bg-2)',
        borderRadius: 12,
        padding: '16px 18px',
        minHeight: 104,
      }}
    >
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-4)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div className="serif tnum" style={{ marginTop: 8, fontSize: 30, fontWeight: 750, color: 'var(--ink)', letterSpacing: '-0.025em' }}>
        {value}
      </div>
      <div style={{ marginTop: 5, fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.4 }}>{detail}</div>
    </div>
  );
}

function CalibrationScatter({ events }: { events: CohortEvent[] }) {
  const points = events.slice(0, 350);
  const maxValue = Math.max(
    0.08,
    ...points.map((event) => Math.max(event.implied, event.realized_abs)),
  );
  const width = 720;
  const height = 320;
  const pad = 42;
  const x = (value: number) => pad + (value / maxValue) * (width - pad * 2);
  const y = (value: number) => height - pad - (value / maxValue) * (height - pad * 2);

  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 14,
        background: 'var(--bg-2)',
        padding: '18px 18px 12px',
        overflow: 'hidden',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'baseline', marginBottom: 10 }}>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--brand-blue-1)', letterSpacing: '0.15em', textTransform: 'uppercase', fontWeight: 700 }}>
            Calibration map
          </div>
          <div style={{ marginTop: 5, fontSize: 13, color: 'var(--ink-2)' }}>Market-implied move vs. realized earnings move magnitude</div>
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-4)' }}>Diagonal = priced exactly</div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Historical implied move versus realized move scatter plot" style={{ width: '100%', height: 'auto', display: 'block' }}>
        <line x1={pad} y1={height - pad} x2={width - pad} y2={pad} stroke="var(--line-2)" strokeWidth="1.5" strokeDasharray="5 5" />
        <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="var(--line)" />
        <line x1={pad} y1={pad} x2={pad} y2={height - pad} stroke="var(--line)" />
        {points.map((event, index) => (
          <circle
            key={`${event.ticker}-${event.date}-${index}`}
            cx={x(event.implied)}
            cy={y(event.realized_abs)}
            r={3.2}
            fill={event.outside_implied ? 'var(--warn)' : 'var(--brand-blue-1)'}
            opacity={0.72}
          >
            <title>{`${event.ticker} ${event.date}: implied ${pct(event.implied)}, realized ${pct(event.realized_abs)}`}</title>
          </circle>
        ))}
        <text x={width / 2} y={height - 8} textAnchor="middle" fill="var(--ink-4)" fontSize="10">Implied move</text>
        <text x="12" y={height / 2} textAnchor="middle" fill="var(--ink-4)" fontSize="10" transform={`rotate(-90 12 ${height / 2})`}>Realized |move|</text>
        <text x={pad} y={height - pad + 17} textAnchor="middle" fill="var(--ink-4)" fontSize="9">0%</text>
        <text x={width - pad} y={height - pad + 17} textAnchor="middle" fill="var(--ink-4)" fontSize="9">{pct(maxValue, 0)}</text>
        <text x={pad - 8} y={pad + 3} textAnchor="end" fill="var(--ink-4)" fontSize="9">{pct(maxValue, 0)}</text>
      </svg>
    </div>
  );
}

export default function ResearchLabClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [data, setData] = useState<CohortResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const queryString = searchParams.toString();
  const setParam = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (!value) next.delete(key);
        else next.set(key, value);
      }
      const qs = next.toString();
      router.replace(qs ? `/research?${qs}` : '/research', { scroll: false });
    },
    [router, searchParams],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`/api/research/cohort${queryString ? `?${queryString}` : ''}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Research cohort unavailable (${response.status})`);
        return (await response.json()) as CohortResponse;
      })
      .then((payload) => setData(payload))
      .catch((reason: unknown) => {
        if ((reason as Error)?.name !== 'AbortError') setError((reason as Error).message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [queryString]);

  const csvHref = useMemo(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.set('format', 'csv');
    return `/api/research/cohort?${params.toString()}`;
  }, [searchParams]);

  const minImpliedPct = searchParams.get('minImplied')
    ? String(Number(searchParams.get('minImplied')) * 100)
    : '';
  const maxImpliedPct = searchParams.get('maxImplied')
    ? String(Number(searchParams.get('maxImplied')) * 100)
    : '';

  const updatePct = (key: 'minImplied' | 'maxImplied', raw: string) => {
    if (!raw.trim()) return setParam({ [key]: null });
    const number = Number(raw);
    if (!Number.isFinite(number)) return;
    setParam({ [key]: String(number / 100) });
  };

  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '34px 28px 72px' }}>
      <section style={{ borderBottom: '1px solid var(--line)', paddingBottom: 28 }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--brand-blue-1)', fontWeight: 700 }}>
          Point-in-time earnings research
        </div>
        <h1
          className="serif qv-m-h1"
          style={{ margin: '14px 0 0', fontSize: 62, lineHeight: 0.95, letterSpacing: '-0.035em', textTransform: 'uppercase', color: 'var(--ink)', fontWeight: 800 }}
        >
          Research Lab
        </h1>
        <p style={{ margin: '18px 0 0', maxWidth: 760, fontSize: 15, lineHeight: 1.65, color: 'var(--ink-2)' }}>
          Build historical earnings cohorts from decision-eligible pre-event option evidence. Compare what the straddle priced with what actually happened, then slice by timing, quarter, EPS outcome, lead time, and implied-move regime.
        </p>
        <div style={{ marginTop: 14, fontSize: 11.5, color: 'var(--ink-4)', maxWidth: 860, lineHeight: 1.5 }}>
          Static EOD research only. Live quote overlays and spot-updated predictions are deliberately excluded from cohort evidence.
        </div>
      </section>

      <section
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          alignItems: 'flex-end',
          padding: '20px 0',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <Field label="Ticker contains">
          <input
            value={searchParams.get('q') ?? ''}
            onChange={(event) => setParam({ q: event.target.value.toUpperCase() || null })}
            placeholder="AAPL"
            style={{ ...controlStyle(), width: 130 }}
          />
        </Field>
        <Field label="Session">
          <select value={searchParams.get('timing') ?? 'all'} onChange={(event) => setParam({ timing: event.target.value === 'all' ? null : event.target.value })} style={controlStyle()}>
            <option value="all">All</option>
            <option value="bmo">BMO</option>
            <option value="amc">AMC</option>
          </select>
        </Field>
        <Field label="Fiscal quarter">
          <select value={searchParams.get('quarter') ?? 'all'} onChange={(event) => setParam({ quarter: event.target.value === 'all' ? null : event.target.value })} style={controlStyle()}>
            <option value="all">All</option>
            <option value="Q1">Q1</option>
            <option value="Q2">Q2</option>
            <option value="Q3">Q3</option>
            <option value="Q4">Q4</option>
          </select>
        </Field>
        <Field label="Vs implied">
          <select value={searchParams.get('outcome') ?? 'all'} onChange={(event) => setParam({ outcome: event.target.value === 'all' ? null : event.target.value })} style={controlStyle()}>
            <option value="all">All</option>
            <option value="outside">Outside</option>
            <option value="inside">Inside</option>
          </select>
        </Field>
        <Field label="EPS surprise">
          <select value={searchParams.get('eps') ?? 'all'} onChange={(event) => setParam({ eps: event.target.value === 'all' ? null : event.target.value })} style={controlStyle()}>
            <option value="all">All</option>
            <option value="beat">Beat</option>
            <option value="miss">Miss</option>
          </select>
        </Field>
        <Field label="Min implied %">
          <input type="number" step="0.5" value={minImpliedPct} onChange={(event) => updatePct('minImplied', event.target.value)} placeholder="4" style={{ ...controlStyle(), width: 90 }} />
        </Field>
        <Field label="Max implied %">
          <input type="number" step="0.5" value={maxImpliedPct} onChange={(event) => updatePct('maxImplied', event.target.value)} placeholder="12" style={{ ...controlStyle(), width: 90 }} />
        </Field>
        <Field label="Sort">
          <select value={searchParams.get('sort') ?? 'date'} onChange={(event) => setParam({ sort: event.target.value === 'date' ? null : event.target.value })} style={controlStyle()}>
            <option value="date">Date</option>
            <option value="implied">Implied</option>
            <option value="realized">Realized</option>
            <option value="edge">Realized − implied</option>
            <option value="ratio">Realized / implied</option>
            <option value="eps">EPS surprise</option>
            <option value="ticker">Ticker</option>
          </select>
        </Field>
        <Field label="Direction">
          <select value={searchParams.get('dir') ?? 'desc'} onChange={(event) => setParam({ dir: event.target.value === 'desc' ? null : event.target.value })} style={controlStyle()}>
            <option value="desc">Desc</option>
            <option value="asc">Asc</option>
          </select>
        </Field>
        <button
          type="button"
          onClick={() => router.replace('/research')}
          style={{ ...controlStyle(), display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}
        >
          <RotateCcw size={13} /> Reset
        </button>
      </section>

      {loading && <div style={{ padding: '42px 0', color: 'var(--ink-3)', fontSize: 13 }}>Building historical cohort…</div>}
      {error && <div style={{ padding: '42px 0', color: 'var(--down)', fontSize: 13 }}>{error}</div>}

      {data && !loading && (
        <>
          <section style={{ padding: '18px 0 8px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ color: 'var(--ink-3)', fontSize: 12 }}>
              <span className="tnum" style={{ color: 'var(--ink)', fontWeight: 700 }}>{data.matching_count}</span> matching events · {data.summary.symbols} symbols
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-4)' }}>
              Source EOD {data.source.source_as_of_max ?? 'unknown'} · {data.source.eligible_event_universe} eligible historical events
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={async () => {
                  await navigator.clipboard.writeText(data.snapshot_id);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1200);
                }}
                style={{ ...controlStyle(), display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}
              >
                <Copy size={13} /> {copied ? 'Copied' : 'Copy cohort ID'}
              </button>
              <a href={csvHref} style={{ ...controlStyle(), display: 'inline-flex', alignItems: 'center', gap: 6, textDecoration: 'none' }}>
                <Download size={13} /> CSV
              </a>
            </div>
          </section>

          <section className="qv-m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12, marginTop: 8 }}>
            <Metric label="Median implied" value={pct(data.summary.medianImplied)} detail="Median decision-eligible pre-event straddle move" />
            <Metric label="Median realized" value={pct(data.summary.medianRealized)} detail="Median absolute close-to-close earnings reaction" />
            <Metric label="Outside implied" value={pct(data.summary.outsideRate)} detail="Share of realized moves larger than the priced move" />
            <Metric label="Median ratio" value={ratio(data.summary.medianRatio)} detail={`IQR ${ratio(data.summary.ratioP25)} – ${ratio(data.summary.ratioP75)}`} />
          </section>

          <section style={{ marginTop: 14 }}>
            <CalibrationScatter events={data.events} />
          </section>

          <section style={{ marginTop: 18, border: '1px solid var(--line)', borderRadius: 14, overflow: 'hidden' }}>
            <div style={{ padding: '14px 16px', background: 'var(--bg-2)', borderBottom: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
              <div>
                <div className="mono" style={{ fontSize: 9.5, letterSpacing: '0.15em', textTransform: 'uppercase', color: 'var(--brand-blue-1)', fontWeight: 700 }}>Event evidence</div>
                <div style={{ marginTop: 4, fontSize: 12, color: 'var(--ink-3)' }}>Only rows with point-in-time eligible option evidence are included.</div>
              </div>
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-4)' }}>{data.returned_count} shown</div>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 1040, borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead>
                  <tr style={{ background: 'var(--bg)' }}>
                    {['Ticker', 'Date', 'Session', 'Quarter', 'Implied', 'Realized |move|', 'R / I', 'Edge', 'ATM IV', 'Lead', 'EPS surprise'].map((label) => (
                      <th key={label} className="mono" style={{ padding: '11px 12px', textAlign: label === 'Ticker' || label === 'Date' ? 'left' : 'right', fontSize: 9.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-4)', borderBottom: '1px solid var(--line)' }}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((event) => (
                    <tr key={`${event.ticker}-${event.date}`} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '11px 12px' }}>
                        <Link href={`/${event.ticker}`} style={{ color: 'var(--ink)', fontWeight: 700 }}>{event.ticker}</Link>
                      </td>
                      <td className="mono tnum" style={{ padding: '11px 12px', color: 'var(--ink-3)' }}>{event.date}</td>
                      <td className="mono" style={{ padding: '11px 12px', textAlign: 'right', color: 'var(--ink-3)' }}>{sessionLabel(event.timing)}</td>
                      <td className="mono" style={{ padding: '11px 12px', textAlign: 'right', color: 'var(--ink-3)' }}>{event.fiscal_q ?? '—'}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right' }}>{pct(event.implied)}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right', color: event.outside_implied ? 'var(--warn)' : 'var(--ink-2)' }}>{pct(event.realized_abs)}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right' }}>{ratio(event.ratio)}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right', color: event.edge > 0 ? 'var(--warn)' : 'var(--ink-3)' }}>{pct(event.edge)}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right' }}>{pct(event.implied_atm_iv)}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right' }}>{event.implied_lead_days == null ? '—' : `T-${event.implied_lead_days}`}</td>
                      <td className="mono tnum" style={{ padding: '11px 12px', textAlign: 'right', color: event.eps_surprise_pct == null ? 'var(--ink-4)' : event.eps_surprise_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>{pct(event.eps_surprise_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', fontSize: 11, color: 'var(--ink-4)', lineHeight: 1.5 }}>
            <span>Snapshot <span className="mono">{data.snapshot_id.slice(0, 24)}…</span></span>
            <span>Decision scope: {data.decision_scope} · live trading eligible: {String(data.live_trading_eligible)}</span>
          </section>
        </>
      )}
    </div>
  );
}
