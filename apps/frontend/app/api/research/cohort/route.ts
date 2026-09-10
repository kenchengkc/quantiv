import { NextResponse } from 'next/server';
import {
  applyCohortQuery,
  canonicalCohortQuery,
  parseCohortQuery,
  summarizeCohort,
  type CohortEvent,
} from '@/lib/researchCohort';
import {
  csvCell,
  readPublicJson,
  researchSnapshotId,
} from '@/lib/researchSnapshot.server';

export const runtime = 'nodejs';

type HistoricalUniverse = {
  schema?: string;
  universe_id?: string;
  generated_at?: string;
  source?: {
    kind?: 'analytical_duckdb' | 'display_payload_fallback';
    completeness?: 'source_level' | 'display_limited';
    as_of_date?: string | null;
    source_revision?: string | null;
    symbol_payloads?: number;
    as_of_min?: string | null;
    as_of_max?: string | null;
    canonical_event_identities_before_cutoff?: number;
  };
  audit?: {
    candidate_event_count?: number;
    eligible_event_count?: number;
    excluded_event_count?: number;
  };
  evidence_rule?: string;
  decision_scope?: string;
  live_trading_eligible?: boolean;
  event_count?: number;
  events?: CohortEvent[];
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
};

function toCsv(id: string, events: CohortEvent[]): string {
  const columns: Array<keyof CohortEvent | 'snapshot_id'> = [
    'snapshot_id',
    'ticker',
    'date',
    'timing',
    'fiscal_q',
    'actual',
    'realized_abs',
    'implied',
    'edge',
    'ratio',
    'outside_implied',
    'implied_as_of',
    'implied_expiration',
    'implied_dte',
    'implied_lead_days',
    'implied_atm_strike',
    'implied_atm_iv',
    'eps_surprise_pct',
    'rev_surprise_pct',
    'implied_quality_status',
  ];
  const lines = [columns.map(csvCell).join(',')];
  for (const event of events) {
    const row: Record<string, unknown> = { snapshot_id: id, ...event };
    lines.push(columns.map((column) => csvCell(row[column])).join(','));
  }
  return `${lines.join('\n')}\n`;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const format = url.searchParams.get('format') === 'csv' ? 'csv' : 'json';
  const query = parseCohortQuery(url.searchParams);
  const universe = readPublicJson<HistoricalUniverse>('research-history.json');
  const universeEvents = Array.isArray(universe?.events) ? universe.events : [];
  if (
    universe?.schema !== 'quantiv.historical-event-universe.v1' ||
    universeEvents.length === 0
  ) {
    return NextResponse.json(
      { error: 'Historical research universe is unavailable.' },
      { status: 503 },
    );
  }

  const allMatching = applyCohortQuery(universeEvents, {
    ...query,
    limit: Math.max(1, universeEvents.length),
  });
  const events = allMatching.slice(0, query.limit);
  const evidence = readPublicJson<ForecastEvidence>('evidence', 'forecast.json');
  const control = readPublicJson<ControlPlane>('control-plane.json');

  // Historical cohorts are an EOD research contract regardless of the current
  // operational/live control snapshot. Never inherit a different live scope.
  const decisionScope = 'end_of_day_research';
  const sourceAsOf = universe.source?.as_of_date ?? universe.source?.as_of_max ?? null;

  const immutable = {
    schema: 'quantiv.historical-cohort.v1',
    source: {
      historical_universe_schema: universe.schema,
      historical_universe_id: universe.universe_id ?? null,
      historical_universe_kind: universe.source?.kind ?? 'display_payload_fallback',
      historical_universe_completeness: universe.source?.completeness ?? 'display_limited',
      historical_universe_generated_at: universe.generated_at ?? null,
      historical_universe_source_revision: universe.source?.source_revision ?? null,
      public_symbol_payloads: universe.source?.symbol_payloads ?? null,
      source_as_of_min: universe.source?.as_of_min ?? sourceAsOf,
      source_as_of_max: universe.source?.as_of_max ?? sourceAsOf,
      source_as_of_date: sourceAsOf,
      source_canonical_events: universe.source?.canonical_event_identities_before_cutoff ?? null,
      eligible_event_universe: universe.event_count ?? universeEvents.length,
      candidate_event_universe: universe.audit?.candidate_event_count ?? null,
      excluded_event_universe: universe.audit?.excluded_event_count ?? null,
      forecast_receipt_id: evidence?.receipt_id ?? null,
      forecast_validated_at: evidence?.validated_at ?? null,
      forecast_quality: evidence?.quality?.status ?? null,
      control_generated_at: control?.generated_at ?? null,
      control_status: control?.status ?? null,
      publication_eligible: control?.publication_eligible ?? null,
    },
    decision_scope: decisionScope,
    live_trading_eligible: false,
    live_quote_overlay_included: false,
    evidence_rule:
      universe.evidence_rule ??
      'decision_eligible_eod pre-event straddle paired with timing-aware realized close-to-close move',
    query: canonicalCohortQuery(query),
    matching_count: allMatching.length,
    returned_count: events.length,
    summary: summarizeCohort(allMatching),
    events,
  };
  const id = researchSnapshotId(immutable);
  const shortId = id.slice('sha256:'.length, 'sha256:'.length + 12);

  if (format === 'csv') {
    return new Response(toCsv(id, events), {
      headers: {
        'Cache-Control': 'private, no-store',
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': `attachment; filename="quantiv-historical-cohort-${shortId}.csv"`,
        'X-Quantiv-Snapshot-Id': id,
        'X-Quantiv-Decision-Scope': decisionScope,
      },
    });
  }

  return NextResponse.json(
    { ...immutable, snapshot_id: id },
    {
      headers: {
        'Cache-Control': 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600',
        'X-Quantiv-Snapshot-Id': id,
        'X-Quantiv-Decision-Scope': decisionScope,
      },
    },
  );
}
