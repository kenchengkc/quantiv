#!/usr/bin/env node
// Rotating price refresher. Runs on a GitHub Actions cron every 5 min.
// Pulls the next batch of symbols from a cursor in Redis, fetches quotes
// from Finnhub (rate-limited to 60/min), and writes them to Redis under the
// same `quote:{SYMBOL}` key that /api/stocks/batch-price reads from.
//
// Symbol universe (hot set, ~800 symbols):
//   • current + next week earnings tickers (from public/weekly.json + weeks/*.json)
//   • every user's watchlist (from Neon)
//
// Env:
//   FINNHUB_API_KEY
//   UPSTASH_REDIS_REST_URL
//   UPSTASH_REDIS_REST_TOKEN
//   DATABASE_URL   (optional — watchlist union only, safe to skip)

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Redis } from '@upstash/redis';
import { neon } from '@neondatabase/serverless';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PUBLIC_DIR = join(REPO_ROOT, 'apps', 'frontend', 'public');
const WEEKS_DIR = join(PUBLIC_DIR, 'weeks');
const SP500_JSON = join(REPO_ROOT, 'lib', 'data', 'sp500-constituents.json');

const BATCH_SIZE = 300;           // symbols per run (60/min × 5 min)
const RATE_LIMIT_PER_MIN = 55;    // leave 5/min headroom for on-demand fetches
const STALE_TTL_S = 60 * 60;      // 1h — long enough to bridge cron gaps
const CURSOR_KEY = 'quote:cursor';

function log(...args) {
  console.log(new Date().toISOString(), '—', ...args);
}

function loadWeekFile(filename) {
  const path = join(WEEKS_DIR, filename);
  if (!existsSync(path)) return [];
  try {
    const payload = JSON.parse(readFileSync(path, 'utf8'));
    return (payload.events ?? []).map((e) => e.ticker).filter(Boolean);
  } catch (e) {
    log('skip', filename, e.message);
    return [];
  }
}

function mondayOf(d) {
  const out = new Date(d);
  const day = out.getDay();
  const delta = day === 0 ? -6 : 1 - day;
  out.setDate(out.getDate() + delta);
  out.setHours(0, 0, 0, 0);
  return out;
}

function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function loadSp500Symbols() {
  if (!existsSync(SP500_JSON)) return [];
  try {
    const rows = JSON.parse(readFileSync(SP500_JSON, 'utf8'));
    return rows.map((r) => r.symbol).filter(Boolean);
  } catch (e) {
    log('sp500 load failed:', e.message);
    return [];
  }
}

async function loadWatchlistSymbols() {
  const url = process.env.DATABASE_URL;
  if (!url) return [];
  try {
    const sql = neon(url);
    const rows = await sql`SELECT DISTINCT symbol FROM watchlist`;
    return rows.map((r) => r.symbol);
  } catch (e) {
    log('watchlist query failed (non-fatal):', e.message);
    return [];
  }
}

async function buildSymbolList() {
  // Priority order (first = refreshed first each cycle):
  //   1. user watchlists
  //   2. this week's earnings tickers
  //   3. next week's earnings tickers
  //   4. S&P 500 constituents
  //   5. last week's earnings tickers
  // Cursor rotates through this priority-ordered list. De-duped via Set;
  // each symbol keeps its FIRST (highest-priority) position.
  const thisMon = mondayOf(new Date());
  const lastMon = new Date(thisMon); lastMon.setDate(thisMon.getDate() - 7);
  const nextMon = new Date(thisMon); nextMon.setDate(thisMon.getDate() + 7);

  const tiers = [
    await loadWatchlistSymbols(),
    loadWeekFile(`${isoDay(thisMon)}.json`),
    loadWeekFile(`${isoDay(nextMon)}.json`),
    loadSp500Symbols(),
    loadWeekFile(`${isoDay(lastMon)}.json`),
  ];

  const ordered = [];
  const seen = new Set();
  for (const tier of tiers) {
    for (const s of tier) {
      if (!seen.has(s)) {
        seen.add(s);
        ordered.push(s);
      }
    }
  }
  log(
    `tiers: watchlist=${tiers[0].length} this=${tiers[1].length} ` +
    `next=${tiers[2].length} sp500=${tiers[3].length} last=${tiers[4].length} ` +
    `total=${ordered.length}`,
  );
  return ordered;
}

async function fetchQuote(symbol, apiKey) {
  const url = `https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(symbol)}&token=${apiKey}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) return null;
  const json = await res.json();
  const price = typeof json.c === 'number' && json.c > 0 ? json.c : null;
  if (price === null) return null;
  return {
    symbol,
    price,
    previousClose: typeof json.pc === 'number' && json.pc > 0 ? json.pc : null,
    change: typeof json.d === 'number' ? json.d : null,
    // Finnhub returns dp as percent; the route stores the decimal form.
    changePct: typeof json.dp === 'number' ? json.dp / 100 : null,
  };
}

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pacedFetch(symbols, apiKey, redis) {
  // Token-bucket pacing: fire at most RATE_LIMIT_PER_MIN/s symbols per second.
  const spacingMs = Math.ceil(60_000 / RATE_LIMIT_PER_MIN);
  let ok = 0;
  let fail = 0;
  const start = Date.now();
  for (const symbol of symbols) {
    const tickStart = Date.now();
    const tick = await fetchQuote(symbol, apiKey);
    if (tick) {
      try {
        await redis.set(`quote:${symbol}`, { at: Date.now(), tick }, { ex: STALE_TTL_S });
        ok++;
      } catch (e) {
        log('redis write failed', symbol, e.message);
        fail++;
      }
    } else {
      fail++;
    }
    const elapsed = Date.now() - tickStart;
    if (elapsed < spacingMs) await sleep(spacingMs - elapsed);
  }
  const duration = ((Date.now() - start) / 1000).toFixed(1);
  log(`fetched ${ok} ok, ${fail} fail, ${duration}s`);
}

async function main() {
  const apiKey = process.env.FINNHUB_API_KEY;
  if (!apiKey) throw new Error('FINNHUB_API_KEY is required');
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) throw new Error('Upstash Redis env vars required');
  const redis = new Redis({ url, token });

  const symbols = await buildSymbolList();
  if (symbols.length === 0) {
    log('no symbols to refresh, exiting');
    return;
  }

  // Cursor wraps around the symbol list. Stable even as the list changes
  // slightly between runs — we just modulo into the current length.
  const raw = await redis.get(CURSOR_KEY);
  const cursor = Number.isFinite(Number(raw)) ? Number(raw) % symbols.length : 0;
  const end = cursor + BATCH_SIZE;
  const batch = [];
  for (let i = cursor; i < end; i++) {
    batch.push(symbols[i % symbols.length]);
  }
  const nextCursor = end % symbols.length;

  log(
    `universe=${symbols.length} batch=${batch.length} cursor ${cursor}→${nextCursor}`,
  );

  await pacedFetch(batch, apiKey, redis);
  await redis.set(CURSOR_KEY, nextCursor);
  log('done');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
