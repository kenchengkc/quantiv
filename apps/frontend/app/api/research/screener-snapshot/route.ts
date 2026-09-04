import { NextResponse } from 'next/server';
import {
  applyScreenerResearchQuery,
  canonicalScreenerQuery,
  parseScreenerResearchQuery,
  type ResearchScreenerEvent,
} from '@/lib/screenerResearch';
import {
  csvCell,
  downloadHeaders,
  readPublicJson,
  researchSnapshotId,
} from '@/lib/researchSnapshot.server';

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

  const bundle = readPublicJson<ScreenerBundle>('screener.json');
  if (!bundle || !Array.isArray(bundle.events)) {
    return NextResponse.json(
      { error: 'The validated screener snapshot is unavailable.' },
      { status: 503 },
    );
  }

  const evidence = readPublicJson<ForecastEvidence>('evidence', 'forecast.json');
  const control = readPublicJson<ControlPlane>('control-plane.json');
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
  const id = researchSnapshotId(immutableBody);
  const payload = { ...immutableBody, snapshot_id: id };
  const asOf = bundle.metadata?.as_of_date ?? 'unknown-date';
  const shortId = id.replace('sha256:', '').slice(0, 12);

  if (format === 'csv') {
    return new Response(toCsv(id, bundle.metadata?.as_of_date ?? null, events), {
      status: 200,
      headers: downloadHeaders(
        id,
        payload.decision_scope,
        `quantiv-screener-${asOf}-${shortId}.csv`,
        'text/csv; charset=utf-8',
      ),
    });
  }

  return new Response(`${JSON.stringify(payload, null, 2)}\n`, {
    status: 200,
    headers: downloadHeaders(
      id,
      payload.decision_scope,
      `quantiv-screener-${asOf}-${shortId}.json`,
      'application/json; charset=utf-8',
    ),
  });
}
