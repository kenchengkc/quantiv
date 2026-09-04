import { NextResponse } from 'next/server';
import {
  csvCell,
  downloadHeaders,
  readPublicJson,
  researchSnapshotId,
} from '@/lib/researchSnapshot.server';

export const runtime = 'nodejs';

const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;

type SymbolDetail = Record<string, unknown> & {
  symbol: string;
  as_of_date?: string | null;
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

function symbolCsv(
  id: string,
  detail: SymbolDetail,
): string {
  const preferred = [
    'symbol',
    'as_of_date',
    'spot_price',
    'next_earnings',
    'next_earnings_timing',
    'expected_move',
    'straddle_features',
    'earnings_history',
    'provider_enrichment',
  ];
  const extras = Object.keys(detail)
    .filter((key) => !preferred.includes(key))
    .sort();
  const columns = ['snapshot_id', ...preferred, ...extras];
  const row: Record<string, unknown> = { snapshot_id: id, ...detail };
  return `${columns.map(csvCell).join(',')}\n${columns.map((column) => csvCell(row[column])).join(',')}\n`;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const symbol = (url.searchParams.get('symbol') ?? '').trim().toUpperCase();
  const format = url.searchParams.get('format') === 'csv' ? 'csv' : 'json';

  if (!SYMBOL_RE.test(symbol)) {
    return NextResponse.json({ error: 'Invalid symbol.' }, { status: 400 });
  }

  const detail = readPublicJson<SymbolDetail>('symbols', `${symbol}.json`);
  if (!detail || detail.symbol?.toUpperCase() !== symbol) {
    return NextResponse.json(
      { error: `No validated research snapshot is available for ${symbol}.` },
      { status: 404 },
    );
  }

  const evidence = readPublicJson<ForecastEvidence>('evidence', 'forecast.json');
  const control = readPublicJson<ControlPlane>('control-plane.json');
  const decisionScope =
    control?.data?.decision_scope ?? 'end_of_day_research';

  const immutableBody = {
    schema: 'quantiv.research-snapshot.v1',
    kind: 'symbol_research',
    source: {
      symbol,
      as_of_date: detail.as_of_date ?? null,
      forecast_receipt_id: evidence?.receipt_id ?? null,
      forecast_validated_at: evidence?.validated_at ?? null,
      forecast_quality: evidence?.quality?.status ?? null,
      control_generated_at: control?.generated_at ?? null,
      control_status: control?.status ?? null,
      publication_eligible: control?.publication_eligible ?? null,
    },
    decision_scope: decisionScope,
    live_trading_eligible: control?.data?.live_trading_eligible ?? false,
    live_quote_overlay_included: false,
    spot_updated_prediction_included: false,
    research: detail,
  };
  const id = researchSnapshotId(immutableBody);
  const payload = { ...immutableBody, snapshot_id: id };
  const asOf = detail.as_of_date ?? 'unknown-date';
  const shortId = id.replace('sha256:', '').slice(0, 12);

  if (format === 'csv') {
    return new Response(symbolCsv(id, detail), {
      status: 200,
      headers: downloadHeaders(
        id,
        decisionScope,
        `quantiv-${symbol}-${asOf}-${shortId}.csv`,
        'text/csv; charset=utf-8',
      ),
    });
  }

  return new Response(`${JSON.stringify(payload, null, 2)}\n`, {
    status: 200,
    headers: downloadHeaders(
      id,
      decisionScope,
      `quantiv-${symbol}-${asOf}-${shortId}.json`,
      'application/json; charset=utf-8',
    ),
  });
}
