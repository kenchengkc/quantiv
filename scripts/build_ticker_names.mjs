#!/usr/bin/env node
/**
 * SEC EDGAR ticker-name ingest.
 *
 * Fetches https://www.sec.gov/files/company_tickers.json (the canonical
 * US-public-company registry — free, no auth, ~10k entries) and writes a
 * normalized `{ ticker: name }` map to apps/frontend/public/ticker-names.json.
 *
 * The frontend's companyName() consults this file as the third fallback,
 * after the hand-curated COMPANY_NAMES map and the S&P 500 constituents
 * JSON. Together those three layers cover ~10k US tickers with sensible
 * brand-formatted names.
 *
 * Run periodically (SEC updates a few times per year):
 *   node scripts/build_ticker_names.mjs
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');
const DEST = path.join(REPO_ROOT, 'apps/frontend/public/ticker-names.json');

const SOURCE = 'https://www.sec.gov/files/company_tickers.json';
const SEC_HEADERS = {
  // SEC requires a descriptive User-Agent for any programmatic access;
  // anonymous requests return 403.
  'User-Agent': 'Quantiv ticker-names ingest (ken@quantiv.app)',
  Accept: 'application/json',
};

// ───── Casing rules ─────────────────────────────────────────────────────
// SEC names arrive in mixed states: "Apple Inc." (already cased) and
// "MICROSOFT CORP" (all caps). We fix the all-caps cases token-by-token
// so already-correct names aren't touched, and tokens that should stay
// uppercase (acronyms, Roman numerals, common business-form abbreviations)
// are preserved.

const SUFFIX_MAP = {
  INC: 'Inc.',
  CORP: 'Corp.',
  CO: 'Co.',
  LTD: 'Ltd.',
  LIMITED: 'Limited',
  HOLDINGS: 'Holdings',
  HOLDING: 'Holding',
  GROUP: 'Group',
  COMPANY: 'Company',
  TRUST: 'Trust',
  PARTNERS: 'Partners',
  TECHNOLOGIES: 'Technologies',
  INDUSTRIES: 'Industries',
  SYSTEMS: 'Systems',
  SOLUTIONS: 'Solutions',
  INTERNATIONAL: 'International',
  COMMUNICATIONS: 'Communications',
  PHARMACEUTICALS: 'Pharmaceuticals',
  RESOURCES: 'Resources',
};

// Tokens that should remain uppercase regardless of context.
const KEEP_UPPER = new Set([
  'LLC', 'PLC', 'SA', 'AG', 'NV', 'SE', 'KG', 'ASA', 'OYJ', 'AB', 'BV',
  'LP', 'LLP', 'PC', 'PA', 'NA', 'USA', 'UK', 'EU', 'US', 'ETF', 'REIT',
  'ADR', 'ETN', 'AI', 'IT', 'PC', 'TV', 'HQ', 'GP',
  'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X',
]);

// Short connector words — lowercased unless first token in the name.
const LOWER_SHORT = new Set([
  'and', 'of', 'the', 'for', 'in', 'to', 'a', 'an', 'on', 'at', 'by',
  'de', 'la', 'le', 'du',
]);

function titleCaseAmpersandToken(tok) {
  // "AT&T" → "AT&T", "S&P" → "S&P", "Procter&Gamble" → "Procter&Gamble"
  return tok.split('&').map((p) => {
    if (!p) return p;
    if (p.length <= 3 && /^[A-Z]+$/.test(p)) return p; // short acronym side
    return p[0] + p.slice(1).toLowerCase();
  }).join('&');
}

function fixToken(tok, isFirst) {
  // Tokens containing a lowercase letter are assumed already-cased.
  if (/[a-z]/.test(tok)) return tok;
  // Tokens with no letters (punctuation, numbers) pass through.
  if (!/[A-Z]/.test(tok)) return tok;

  // Strip trailing punctuation for lookup, re-attach after.
  const trailing = tok.match(/[.,;:]+$/)?.[0] ?? '';
  const core = trailing ? tok.slice(0, -trailing.length) : tok;
  const upper = core.toUpperCase();

  if (SUFFIX_MAP[upper]) return SUFFIX_MAP[upper] + (trailing.includes('.') ? '' : '');
  if (KEEP_UPPER.has(upper)) return core + trailing;
  if (!isFirst && LOWER_SHORT.has(upper.toLowerCase())) return upper.toLowerCase() + trailing;
  if (core.includes('&')) return titleCaseAmpersandToken(core) + trailing;

  // Default: capitalize first letter, lowercase the rest.
  return core[0] + core.slice(1).toLowerCase() + trailing;
}

function fixCasing(name) {
  // Split preserving whitespace runs so we can rejoin exactly.
  const parts = name.split(/(\s+)/);
  let nonWsIdx = 0;
  return parts
    .map((part) => {
      if (/^\s+$/.test(part) || part === '') return part;
      const isFirst = nonWsIdx === 0;
      nonWsIdx += 1;
      return fixToken(part, isFirst);
    })
    .join('')
    .trim();
}

// ───── Fetch & write ────────────────────────────────────────────────────
async function main() {
  console.log(`Fetching ${SOURCE}…`);
  const res = await fetch(SOURCE, { headers: SEC_HEADERS });
  if (!res.ok) {
    throw new Error(`SEC EDGAR returned ${res.status} ${res.statusText}`);
  }
  const data = await res.json();

  const out = {};
  let normalized = 0;
  let skipped = 0;
  for (const v of Object.values(data)) {
    if (!v || typeof v !== 'object') continue;
    const { ticker, title } = /** @type {{ ticker?: string; title?: string }} */ (v);
    if (!ticker || !title) { skipped += 1; continue; }
    const tk = String(ticker).toUpperCase().trim();
    const raw = String(title).trim();
    const fixed = fixCasing(raw);
    if (fixed !== raw) normalized += 1;
    // First-wins for duplicates (SEC's iteration order roughly matches
    // primary listings).
    if (!(tk in out)) out[tk] = fixed;
  }

  const count = Object.keys(out).length;
  console.log(
    `Parsed ${count} tickers (${normalized} casing-normalized, ${skipped} skipped)`,
  );

  await fs.mkdir(path.dirname(DEST), { recursive: true });
  // Compact JSON (no pretty-printing) keeps the served file ~30% smaller.
  await fs.writeFile(DEST, JSON.stringify(out) + '\n');
  const stat = await fs.stat(DEST);
  console.log(`Wrote ${DEST} (${(stat.size / 1024).toFixed(1)} kB)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
