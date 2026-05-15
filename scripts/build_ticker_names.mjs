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

function fixToken(tok, isFirst, ticker) {
  // Dotted abbreviation like "S.A.", "S.a.", "N.V.", "P.L.C." — force
  // uppercase. SEC occasionally lowercases the trailing letter of these
  // legal-form suffixes ("Banco Santander (Brasil) S.a."); normalize.
  // Pattern: 2-4 single-letter segments separated by periods, optional
  // trailing period.
  if (/^([A-Za-z]\.){1,3}[A-Za-z]\.?$/.test(tok)) {
    const upperized = tok.replace(/[a-z]/g, (c) => c.toUpperCase());
    // Ensure a trailing period.
    return upperized.endsWith('.') ? upperized : `${upperized}.`;
  }

  // Parenthesized token like "(SGHC)" — process inside content with the
  // same rules but preserve uppercase for short all-caps acronyms inside.
  // (The whole-name pre-pass already uppercased lowercase inner content,
  // so here we mainly need to stop the default rule from lowercasing
  // the inside of "(SGHC)".)
  const parenMatch = tok.match(/^(\()([A-Za-z]+)(\)[.,;:]?)$/);
  if (parenMatch) {
    const [, open, inner, close] = parenMatch;
    if (/^[A-Z]+$/.test(inner) && inner.length <= 5) {
      return `${open}${inner}${close}`;
    }
    // Mixed-case or long content — title-case the first letter.
    return `${open}${inner[0].toUpperCase()}${inner.slice(1).toLowerCase()}${close}`;
  }

  // Strip trailing punctuation for lookup, re-attach after.
  const trailing = tok.match(/[.,;:]+$/)?.[0] ?? '';
  const core = trailing ? tok.slice(0, -trailing.length) : tok;
  const upper = core.toUpperCase();

  // Suffix normalization runs regardless of original case, so
  // "Co" / "CO" / "co" all collapse to "Co." with the conventional
  // period. SEC mixes these inside otherwise-correct strings (e.g.
  // "Procter & Gamble Co"), and this normalizes the punctuation too.
  if (SUFFIX_MAP[upper]) return SUFFIX_MAP[upper];

  // Tokens with mixed case are assumed already-correct beyond the
  // suffix rule above — leave them as-is.
  if (/[a-z]/.test(core)) return tok;
  // Tokens with no letters at all (punctuation, pure numbers).
  if (!/[A-Z]/.test(core)) return tok;

  if (KEEP_UPPER.has(upper)) return core + trailing;
  if (!isFirst && LOWER_SHORT.has(upper.toLowerCase())) {
    return upper.toLowerCase() + trailing;
  }
  if (core.includes('&')) return titleCaseAmpersandToken(core) + trailing;

  // Brand-acronym first-token rule: if this is the first token, all
  // uppercase, 2-3 letters long, AND its uppercase form equals the
  // ticker, it's an unambiguous brand acronym — preserve uppercase.
  // "NVR INC" (ticker NVR) → "NVR Inc.", "IBM CORP" (ticker IBM) → "IBM
  // Corp.". Length cap at 3 deliberately excludes 4-char word-names like
  // ROKU and PEAR that should still title-case (Roku, Pear).
  if (
    isFirst &&
    ticker &&
    /^[A-Z]+$/.test(core) &&
    core.length >= 2 &&
    core.length <= 3 &&
    core === ticker.toUpperCase()
  ) {
    return core + trailing;
  }

  // Short alphanumeric tokens containing a digit (3M, 3D, 7-Eleven's
  // "7") are brand IDs — preserve their uppercase letters. Pure-letter
  // acronyms (IBM, GE) deliberately fall through to title-casing: the
  // famous ones are covered by the hand-curated COMPANY_NAMES and the
  // S&P 500 JSON, which both take priority over this EDGAR fallback.
  if (core.length <= 4 && /\d/.test(core) && /^[A-Z0-9]+$/.test(core)) {
    return core + trailing;
  }

  // Default: capitalize first letter, lowercase the rest. Then restore
  // uppercase for any short parenthesized acronyms inside the token —
  // catches things like "STRATS(SM)" which is a single whitespace-token
  // but contains an inline "(SM)" marker that should stay uppercase.
  const titled = core[0] + core.slice(1).toLowerCase();
  const withInlineCaps = titled.replace(
    /\(([A-Za-z]{2,5})\)/g,
    (_, inner) => `(${inner.toUpperCase()})`,
  );
  return withInlineCaps + trailing;
}

function fixCasing(name, ticker) {
  // Pre-clean: strip SEC artifacts that aren't useful display text.
  let cleaned = name
    // Trailing /XX/ or /XX (state-of-incorporation, /ADR, /PFD, etc.).
    // SEC uses both forms — "8X8 Inc. /de/", "Puig Brands S.A./ADR".
    .replace(/\s*\/[A-Za-z]{2,4}\/?\s*$/, '')
    // Trailing parenthesized 2-3 letter state code: "(DE)", "(de)".
    .replace(/\s*\([A-Za-z]{2,3}\)\s*$/, '')
    .trim();

  // Force uppercase on 2-5 letter parenthesized content anywhere in the
  // name — these are typically ticker codes, brand acronyms, or ISO codes
  // SEC stored partially lowercased ("Super Group (sghc)" → "(SGHC)").
  // Longer parenthesized content (e.g. "(Brasil)", "(Cayman)") stays as-is.
  cleaned = cleaned.replace(
    /\(([A-Za-z]{2,5})\)/g,
    (_, inner) => `(${inner.toUpperCase()})`,
  );

  // Whole name is a single short all-caps token — a brand acronym (RH,
  // VTEX, BBBY). Preserve. Mid-sentence all-caps tokens (e.g. the "ELI"
  // in "ELI LILLY") still get title-cased by the per-token rules below.
  if (/^[A-Z]+$/.test(cleaned) && cleaned.length <= 5) return cleaned;

  // Split preserving whitespace runs so we can rejoin exactly.
  const parts = cleaned.split(/(\s+)/);
  let nonWsIdx = 0;
  return parts
    .map((part) => {
      if (/^\s+$/.test(part) || part === '') return part;
      const isFirst = nonWsIdx === 0;
      nonWsIdx += 1;
      return fixToken(part, isFirst, ticker);
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
    const fixed = fixCasing(raw, tk);
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
