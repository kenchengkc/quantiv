#!/usr/bin/env node

import { existsSync, readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = dirname(HERE);
const PUBLIC = join(FRONTEND_ROOT, 'public');
const SYMBOLS = join(PUBLIC, 'symbols');
const OUTPUT = join(PUBLIC, 'research-history.json');
const TEMP = `${OUTPUT}.tmp`;

function finite(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function sourceLevelArtifact() {
  if (!existsSync(OUTPUT)) return null;
  let payload;
  try {
    payload = JSON.parse(readFileSync(OUTPUT, 'utf8'));
  } catch {
    return null;
  }
  if (
    payload?.schema !== 'quantiv.historical-event-universe.v1' ||
    payload?.source?.kind !== 'analytical_duckdb' ||
    payload?.source?.completeness !== 'source_level'
  ) {
    return null;
  }
  if (!Array.isArray(payload.events) || payload.event_count !== payload.events.length) {
    throw new Error('source-level research history has an invalid event count');
  }
  if (!payload.universe_id || !String(payload.universe_id).match(/^sha256:[0-9a-f]{64}$/)) {
    throw new Error('source-level research history has no content-addressed universe id');
  }
  return payload;
}

const retained = sourceLevelArtifact();
if (retained) {
  console.log(
    `Research history: preserving ${retained.event_count} source-level events ` +
      `(${retained.universe_id.slice(0, 19)}…) -> ${OUTPUT}`,
  );
  process.exit(0);
}

function buildPreviewUniverse() {
  if (!existsSync(SYMBOLS)) {
    throw new Error(`symbol research directory is missing: ${SYMBOLS}`);
  }

  const events = [];
  const asOfDates = new Set();
  let payloadCount = 0;

  for (const filename of readdirSync(SYMBOLS).filter((name) => name.endsWith('.json')).sort()) {
    let payload;
    try {
      payload = JSON.parse(readFileSync(join(SYMBOLS, filename), 'utf8'));
    } catch (error) {
      throw new Error(`invalid symbol payload ${filename}: ${error.message}`);
    }

    const ticker = String(payload.symbol || filename.slice(0, -5)).trim().toUpperCase();
    if (!ticker) continue;
    payloadCount += 1;
    if (payload.as_of_date) asOfDates.add(String(payload.as_of_date));

    for (const row of Array.isArray(payload.earnings_history) ? payload.earnings_history : []) {
      if (
        !row?.date ||
        !row?.implied_as_of ||
        row?.implied_quality_status !== 'decision_eligible_eod' ||
        !finite(row?.actual) ||
        !finite(row?.implied) ||
        row.implied <= 0
      ) {
        continue;
      }

      const realizedAbs = Math.abs(row.actual);
      events.push({
        ticker,
        date: String(row.date),
        timing: String(row.timing || 'unknown'),
        fiscal_q: row.fiscal_q ?? null,
        actual: row.actual,
        realized_abs: realizedAbs,
        implied: row.implied,
        implied_as_of: String(row.implied_as_of),
        implied_expiration: row.implied_expiration ?? null,
        implied_dte: finite(row.implied_dte) ? row.implied_dte : null,
        implied_lead_days: finite(row.implied_lead_days) ? row.implied_lead_days : null,
        implied_atm_strike: finite(row.implied_atm_strike) ? row.implied_atm_strike : null,
        implied_atm_iv: finite(row.implied_atm_iv) ? row.implied_atm_iv : null,
        implied_quality_status: 'decision_eligible_eod',
        eps_surprise_pct: finite(row.eps_surprise_pct) ? row.eps_surprise_pct : null,
        rev_surprise_pct: finite(row.rev_surprise_pct) ? row.rev_surprise_pct : null,
        edge: realizedAbs - row.implied,
        ratio: realizedAbs / row.implied,
        outside_implied: realizedAbs > row.implied,
      });
    }
  }

  events.sort((a, b) => b.date.localeCompare(a.date) || a.ticker.localeCompare(b.ticker));
  const dates = Array.from(asOfDates).sort();
  return {
    schema: 'quantiv.historical-event-universe.preview.v1',
    source: {
      kind: 'display_payload_fallback',
      completeness: 'display_limited',
      symbol_payloads: payloadCount,
      as_of_min: dates[0] ?? null,
      as_of_max: dates[dates.length - 1] ?? null,
    },
    evidence_rule:
      'display-limited decision_eligible_eod rows retained from per-symbol payloads',
    decision_scope: 'end_of_day_research',
    live_trading_eligible: false,
    event_count: events.length,
    events,
  };
}

const payload = buildPreviewUniverse();
writeFileSync(TEMP, `${JSON.stringify(payload, null, 2)}\n`);
renameSync(TEMP, OUTPUT);
console.log(
  `Research history preview: ${payload.event_count} display-limited events from ` +
    `${payload.source.symbol_payloads} symbol payloads -> ${OUTPUT}`,
);
