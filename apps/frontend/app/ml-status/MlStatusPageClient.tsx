'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  RefreshCw,
  Server,
} from 'lucide-react';
import {
  classifyStatus,
  compactNumber,
  formatDateTime,
  freshCoverageRatio,
  importRowDelta,
  pct,
  sortedHorizonRows,
  sortedImportHorizons,
  sortedModelRows,
  type MlStatusImportRow,
  type MlStatusModelRow,
  type MlStatusResponse,
  type StatusKind,
} from './statusViewModel';

type LoadState = {
  status: 'loading' | 'ready' | 'error';
  payload: MlStatusResponse | null;
  error: string | null;
  updatedAt: number | null;
};

const EMPTY_STATE: LoadState = {
  status: 'loading',
  payload: null,
  error: null,
  updatedAt: null,
};

function statusColor(kind: StatusKind): string {
  if (kind === 'ok') return 'var(--up)';
  if (kind === 'degraded') return 'var(--flag)';
  return 'var(--down)';
}

function statusLabel(kind: StatusKind): string {
  if (kind === 'ok') return 'Operational';
  if (kind === 'degraded') return 'Degraded';
  return 'Unavailable';
}

function Metric({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'neutral' | 'accent' | 'up' | 'warn' | 'down';
}) {
  const color = {
    neutral: 'var(--ink)',
    accent: 'var(--accent)',
    up: 'var(--up)',
    warn: 'var(--flag)',
    down: 'var(--down)',
  }[tone];

  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 8,
        padding: '14px 16px',
        minHeight: 92,
        display: 'grid',
        alignContent: 'space-between',
        gap: 12,
        background: 'color-mix(in oklab, var(--bg-2) 72%, transparent)',
      }}
    >
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--ink-4)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {label}
      </div>
      <div>
        <div
          className="mono tnum"
          style={{
            color,
            fontSize: 22,
            lineHeight: 1.1,
            fontWeight: 600,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {value}
        </div>
        {sub ? (
          <div
            style={{
              marginTop: 5,
              color: 'var(--ink-3)',
              fontSize: 11,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {sub}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Pill({
  children,
  tone = 'neutral',
  title,
}: {
  children: React.ReactNode;
  tone?: 'neutral' | 'up' | 'warn' | 'down' | 'accent';
  title?: string;
}) {
  const color = {
    neutral: 'var(--ink-3)',
    up: 'var(--up)',
    warn: 'var(--flag)',
    down: 'var(--down)',
    accent: 'var(--accent)',
  }[tone];

  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 24,
        borderRadius: 999,
        padding: '0 9px',
        border: `1px solid color-mix(in oklab, ${color} 34%, transparent)`,
        color,
        background: `color-mix(in oklab, ${color} 12%, transparent)`,
        fontSize: 10,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

function Section({
  title,
  icon,
  children,
  aside,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <section style={{ marginTop: 30 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 18,
          marginBottom: 12,
        }}
      >
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 9 }}>
          <span style={{ color: 'var(--accent)', display: 'inline-flex' }}>{icon}</span>
          <h2
            className="serif"
            style={{
              margin: 0,
              fontSize: 18,
              lineHeight: 1.2,
              fontWeight: 700,
              color: 'var(--ink)',
            }}
          >
            {title}
          </h2>
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

function DataTable({
  columns,
  children,
}: {
  columns: string[];
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        overflowX: 'auto',
        border: '1px solid var(--line)',
        borderRadius: 8,
        background: 'color-mix(in oklab, var(--bg-2) 58%, transparent)',
      }}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                style={{
                  padding: '11px 14px',
                  borderBottom: '1px solid var(--line)',
                  color: 'var(--ink-4)',
                  fontSize: 10,
                  letterSpacing: '0.12em',
                  textTransform: 'uppercase',
                  textAlign: 'left',
                  whiteSpace: 'nowrap',
                }}
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Td({
  children,
  muted = false,
}: {
  children: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <td
      style={{
        padding: '12px 14px',
        borderBottom: '1px solid var(--line)',
        color: muted ? 'var(--ink-4)' : 'var(--ink-2)',
        fontSize: 12,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </td>
  );
}

function LatestImport({ latestImport }: { latestImport: MlStatusImportRow | null }) {
  if (!latestImport) {
    return (
      <div
        style={{
          border: '1px solid var(--line)',
          borderRadius: 8,
          padding: 18,
          color: 'var(--ink-3)',
          background: 'color-mix(in oklab, var(--bg-2) 58%, transparent)',
        }}
      >
        No import audit row has been recorded yet.
      </div>
    );
  }

  const rowDelta = importRowDelta(latestImport);
  const horizons = sortedImportHorizons(latestImport);

  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 8,
        padding: 18,
        background: 'color-mix(in oklab, var(--bg-2) 58%, transparent)',
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) auto',
          gap: 16,
          alignItems: 'start',
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            className="mono"
            style={{
              color: 'var(--ink)',
              fontSize: 13,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={latestImport.parquet_file}
          >
            {latestImport.parquet_file}
          </div>
          <div style={{ marginTop: 5, color: 'var(--ink-3)', fontSize: 12 }}>
            Imported {formatDateTime(latestImport.imported_at)} · {latestImport.import_mode}
          </div>
        </div>
        <Pill tone={rowDelta && rowDelta > 0 ? 'warn' : 'up'}>
          Δ {rowDelta == null ? '—' : compactNumber(rowDelta)}
        </Pill>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
          marginTop: 18,
        }}
      >
        <Metric label="Source rows" value={compactNumber(latestImport.source_rows)} />
        <Metric label="Rows upserted" value={compactNumber(latestImport.rows_upserted)} tone="accent" />
        <Metric label="Feature vectors" value={compactNumber(latestImport.feature_vector_rows)} />
        <Metric label="Events" value={compactNumber(latestImport.distinct_events)} />
      </div>

      <div
        style={{
          display: 'flex',
          gap: 8,
          flexWrap: 'wrap',
          marginTop: 16,
          alignItems: 'center',
        }}
      >
        <Pill>Snapshots {latestImport.min_snapshot_date ?? '—'} → {latestImport.max_snapshot_date ?? '—'}</Pill>
        <Pill>Duplicate keys {compactNumber(latestImport.duplicate_keys)}</Pill>
        {horizons.map(([horizon, count]) => (
          <Pill key={horizon} tone="accent">T{horizon} {compactNumber(count)}</Pill>
        ))}
      </div>
    </div>
  );
}

function ModelStatusPill({ model }: { model: MlStatusModelRow }) {
  if (!model.point_model_exists) return <Pill tone="down">Missing</Pill>;
  if (model.loaded) return <Pill tone="up">Loaded</Pill>;
  return <Pill tone="neutral">On disk</Pill>;
}

export default function MlStatusPageClient() {
  const [state, setState] = useState<LoadState>(EMPTY_STATE);

  const load = useCallback(async (signal?: AbortSignal) => {
    setState((prev) => ({
      ...prev,
      status: prev.payload ? 'ready' : 'loading',
      error: null,
    }));
    try {
      const res = await fetch('/api/ml/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
        signal,
        body: JSON.stringify({ fresh_window_days: 7 }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok) {
        const message = json && typeof json.error === 'string'
          ? json.error
          : `status ${res.status}`;
        throw new Error(message);
      }
      setState({
        status: 'ready',
        payload: json as MlStatusResponse,
        error: null,
        updatedAt: Date.now(),
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setState((prev) => ({
        status: prev.payload ? 'ready' : 'error',
        payload: prev.payload,
        error: err instanceof Error ? err.message : 'ML status request failed',
        updatedAt: prev.updatedAt,
      }));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [load]);

  const payload = state.payload;
  const kind = classifyStatus(payload);
  const coverageRatio = freshCoverageRatio(payload);
  const horizonRows = useMemo(
    () => sortedHorizonRows(payload?.rows_by_horizon ?? []),
    [payload?.rows_by_horizon],
  );
  const modelRows = useMemo(
    () => sortedModelRows(payload?.models ?? []),
    [payload?.models],
  );

  return (
    <div className="qv-m-pad" style={{ maxWidth: 1120, margin: '0 auto', padding: '28px 28px 64px' }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          gap: 20,
          borderBottom: '1px solid var(--line)',
          paddingBottom: 20,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              color: statusColor(kind),
              marginBottom: 8,
            }}
          >
            {kind === 'ok' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
            <span
              className="mono"
              style={{
                fontSize: 11,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
              }}
            >
              {statusLabel(kind)}
            </span>
          </div>
          <h1
            className="serif"
            style={{
              margin: 0,
              color: 'var(--ink)',
              fontSize: 32,
              lineHeight: 1.05,
              fontWeight: 800,
              letterSpacing: 0,
            }}
          >
            ML Backend Status
          </h1>
          <div style={{ marginTop: 8, color: 'var(--ink-3)', fontSize: 13 }}>
            Backend serving, feature coverage, import counts, and model inventory.
          </div>
        </div>

        <button
          type="button"
          onClick={() => void load()}
          aria-label="Refresh ML status"
          title="Refresh ML status"
          style={{
            width: 38,
            height: 38,
            display: 'grid',
            placeItems: 'center',
            borderRadius: 999,
            border: '1px solid var(--line-2)',
            background: 'var(--bg-2)',
            color: 'var(--ink-2)',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <RefreshCw size={16} style={{ animation: state.status === 'loading' ? 'qv-spin 900ms linear infinite' : undefined }} />
        </button>
      </header>

      {state.error ? (
        <div
          style={{
            marginTop: 16,
            border: '1px solid color-mix(in oklab, var(--down) 34%, var(--line))',
            borderRadius: 8,
            padding: '12px 14px',
            color: 'var(--down)',
            background: 'color-mix(in oklab, var(--down) 10%, transparent)',
            fontSize: 12,
          }}
        >
          {state.error}
        </div>
      ) : null}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
          gap: 12,
          marginTop: 22,
        }}
      >
        <Metric
          label="Serving"
          value={statusLabel(kind)}
          sub={payload ? `Checked ${formatDateTime(payload.checked_at)}` : 'Loading'}
          tone={kind === 'ok' ? 'up' : kind === 'degraded' ? 'warn' : 'down'}
        />
        <Metric
          label="Fresh rows"
          value={payload?.data ? compactNumber(payload.data.fresh_feature_rows) : '—'}
          sub={coverageRatio == null ? `Window ${payload?.fresh_window_days ?? 7}d` : `${pct(coverageRatio)} of total`}
          tone="accent"
        />
        <Metric
          label="Fresh events"
          value={payload?.data ? compactNumber(payload.data.fresh_distinct_events) : '—'}
          sub={payload?.data ? `${compactNumber(payload.data.fresh_distinct_symbols)} symbols` : undefined}
        />
        <Metric
          label="Models"
          value={payload ? compactNumber(payload.available_model_horizons.length) : '—'}
          sub={payload ? `${compactNumber(payload.loaded_model_horizons.length)} loaded` : undefined}
        />
        <Metric
          label="Runtime"
          value={payload?.postgres_available ? 'Postgres' : 'No DB'}
          sub={payload?.redis_available ? 'Redis cache online' : 'Redis unavailable'}
          tone={payload?.postgres_available ? 'up' : 'down'}
        />
        <Metric
          label="Latest score"
          value={payload?.data?.latest_snapshot_date ?? '—'}
          sub={formatDateTime(payload?.data?.latest_scored_at)}
        />
      </div>

      <Section
        title="Coverage By Horizon"
        icon={<Database size={18} />}
        aside={<Pill tone="accent">{payload?.fresh_window_days ?? 7}d fresh window</Pill>}
      >
        <DataTable columns={['Horizon', 'Fresh rows', 'Total rows', 'Fresh share', 'Latest snapshot', 'Latest score']}>
          {horizonRows.length === 0 ? (
            <tr><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td></tr>
          ) : horizonRows.map((row) => {
            const ratio = row.total_feature_rows > 0
              ? row.fresh_feature_rows / row.total_feature_rows
              : null;
            return (
              <tr key={row.horizon_days}>
                <Td><span className="mono">T{row.horizon_days}</span></Td>
                <Td>{compactNumber(row.fresh_feature_rows)}</Td>
                <Td muted>{compactNumber(row.total_feature_rows)}</Td>
                <Td>{pct(ratio)}</Td>
                <Td muted>{row.latest_snapshot_date ?? '—'}</Td>
                <Td muted>{formatDateTime(row.latest_scored_at)}</Td>
              </tr>
            );
          })}
        </DataTable>
      </Section>

      <Section title="Latest Import" icon={<Clock size={18} />}>
        <LatestImport latestImport={payload?.latest_import ?? null} />
      </Section>

      <Section
        title="Model Inventory"
        icon={<Server size={18} />}
        aside={<Pill>{payload?.models_dir ?? 'models dir'}</Pill>}
      >
        <DataTable columns={['Horizon', 'State', 'Quantiles', 'Features', 'Version', 'Val MAE', 'Trained']}>
          {modelRows.length === 0 ? (
            <tr><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td><Td muted>—</Td></tr>
          ) : modelRows.map((model) => (
            <tr key={model.horizon_days}>
              <Td><span className="mono">T{model.horizon_days}</span></Td>
              <Td><ModelStatusPill model={model} /></Td>
              <Td>{compactNumber(model.quantile_model_count)}</Td>
              <Td muted>{compactNumber(model.feature_count)}</Td>
              <Td muted>{model.model_version ?? '—'}</Td>
              <Td muted>{model.val_mae == null ? '—' : model.val_mae.toFixed(4)}</Td>
              <Td muted>{formatDateTime(model.trained_at)}</Td>
            </tr>
          ))}
        </DataTable>
      </Section>

      <Section
        title="Dependencies"
        icon={<Activity size={18} />}
        aside={state.updatedAt ? <Pill>Updated {formatDateTime(new Date(state.updatedAt).toISOString())}</Pill> : undefined}
      >
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <Pill tone={payload?.postgres_available ? 'up' : 'down'}>
            Postgres {payload?.postgres_available ? 'online' : 'offline'}
          </Pill>
          <Pill tone={payload?.redis_available ? 'up' : 'warn'}>
            Redis {payload?.redis_available ? 'online' : 'unavailable'}
          </Pill>
          <Pill tone={(payload?.available_model_horizons.length ?? 0) > 0 ? 'up' : 'down'}>
            Models {compactNumber(payload?.available_model_horizons.length)}
          </Pill>
          <Pill tone={payload?.data && payload.data.total_feature_rows > 0 ? 'up' : 'warn'}>
            Feature rows {compactNumber(payload?.data?.total_feature_rows)}
          </Pill>
        </div>
      </Section>

      <style jsx>{`
        @keyframes qv-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        tbody tr:last-child td {
          border-bottom: none !important;
        }
      `}</style>
    </div>
  );
}
