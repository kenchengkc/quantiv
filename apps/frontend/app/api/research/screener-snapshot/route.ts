import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { NextResponse } from 'next/server';
import {
  applyScreenerResearchQuery,
  canonicalScreenerQuery,
  parseScreenerResearchQuery,
  type ResearchScreenerEvent,
} from '@/lib/screenerResearch';

export const runtime = 'nodejs';

type ScreenerBundle = {
  metadata?: {
    version?: string;
    as_of_date?: string;
    generated_at?: string;
    event_count?: number;
    week_starts?: string[];
  };
  events?: ResearchScreenerEvent[];
};

type ForecastEvidence = {
  receipt_id?: string;
  validated_at?: string;
  quality?: { status?: string };
};

type ControlPlane = {
  generated_at?: string;
  status?: string;
  publication_eligible?: boolean;
  data?: { decision_scope?: string; live_trading_eligible?: boolean };
};

function publicPath(...parts: string[]): string | null {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public', ...parts),
    join(process.cwd(), 'public', ...parts),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function readJson<T>(...parts: string[]): T | null {
  const path = publicPath(...parts);
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as T;
  } catch {
    return null;
  }
}

function canonicalJson(value: unknown): string {
  if (value === undefined) return 'null';
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value) ?? 'null';
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  }
  const object = value as Record<string, unknown>;
  const entries = Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`);
  return `{${entries.join(',')}}`;
}

function snapshotId(body: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalJson(body)).digest('hex')}`;
}

function csvCell(value: unknown): string {
  if (value == null) return '';
  const text =
    typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function toCsv(
  id: string,
  asOfDate: string | null,
  events: ResearchScreenerEvent[],
): string {
  const preferred = [
    'ticker',
    'earnings_date',
    'timing',
    'fiscal_q',
    'spot_price',
    'atm_iv',
    'em_straddle_pct',
    'em_ml_pct',
    'p10',
    'p90',
    'iv_rank',
    'hist_move_avg_4q',
    'iv_crush_pct',
    'lead_time_days',
    'days_to_expiry',
    'skew_atm',
    'term_slope',
    'em_method',
  ];
  const extras = Array.from(
    new Set(events.flatMap((event) => Object.keys(event))),
  )
    .filter((key) => !preferred.includes(key))
    .sort();
  const columns = ['snapshot_id', 'source_as_of_date', ...preferred, ...extras];
  const lines = [columns.map(csvCell).join(',')];
  for (const event of events) {
    const row: Record<string, unknown> = {
      snapshot_id: id,
      source_as_of_date: asOfDate,
      ...event,
    };
    lines.push(columns.map((column) => csvCell(row[column])).join(','));
  }
  return `${lines.join('\n')}\n`;
}

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const format = requestUrl.searchParams.get('format') === 'csv' ? 'csv' : 'json';
  const query = parseScreenerResearchQuery(requestUrl.searchParams);

  const bundle = readJson<ScreenerBundle>('screener.json');
  if (!bundle || !Array.isArray(bundle.events)) {
    return NextResponse.json(
      { error: 'The validated screener snapshot is unavailable.' },
      { status: 503 },
    );
  }

  const evidence = readJson<ForecastEvidence>('evidence', 'forecast.json');
  const control = readJson<ControlPlane>('control-plane.json');
  const events = applyScreenerResearchQuery(bundle.events, query);

  const immutableBody = {
    schema: 'quantiv.research-snapshot.v1',
    kind: 'earnings_screener',
    source: {
      screener_version: bundle.metadata?.version ?? null,
      as_of_date: bundle.metadata?.as_of_date ?? null,
      generated_at: bundle.metadata?.generated_at ?? null,
      source_event_count: bundle.metadata?.event_count ?? bundle.events.length,
      week_starts: bundle.metadata?.week_starts ?? [],
      forecast_receipt_id: evidence?.receipt_id ?? null,
      forecast_validated_at: evidence?.validated_at ?? null,
      forecast_quality: evidence?.quality?.status ?? null,
      control_generated_at: control?.generated_at ?? null,
      control_status: control?.status ?? null,
      publication_eligible: control?.publication_eligible ?? null,
    },
    decision_scope:
      control?.data?.decision_scope ?? 'end_of_day_research',
    live_trading_eligible: control?.data?.live_trading_eligible ?? false,
    live_quote_overlay_included: false,
    query: canonicalScreenerQuery(query),
    result_count: events.length,
    events,
  };
  const id = snapshotId(immutableBody);
  const payload = { ...immutableBody, snapshot_id: id };
  const asOf = bundle.metadata?.as_of_date ?? 'unknown-date';
  const shortId = id.replace('sha256:', '').slice(0, 12);

  const headers = new Headers({
    'Cache-Control': 'private, no-store',
    'X-Quantiv-Snapshot-Id': id,
    'X-Quantiv-Decision-Scope': payload.decision_scope,
  });

  if (format === 'csv') {
    headers.set('Content-Type', 'text/csv; charset=utf-8');
    headers.set(
      'Content-Disposition',
      `attachment; filename="quantiv-screener-${asOf}-${shortId}.csv"`,
    );
    return new Response(toCsv(id, bundle.metadata?.as_of_date ?? null, events), {
      status: 200,
      headers,
    });
  }

  headers.set('Content-Type', 'application/json; charset=utf-8');
  headers.set(
    'Content-Disposition',
    `attachment; filename="quantiv-screener-${asOf}-${shortId}.json"`,
  );
  return new Response(`${JSON.stringify(payload, null, 2)}\n`, {
    status: 200,
    headers,
  });
}
