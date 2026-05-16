import sp500Constituents from '../../../lib/data/sp500-constituents.json';

// 503-row S&P 500 lookup (symbol → company name). Used as the fallback
// when the hand-curated COMPANY_NAMES map below doesn't have an entry,
// so tickers like EA, JBL, KMX render their real company names on the
// screener / detail / watchlist instead of just echoing the symbol.
//
// The source list (Wikipedia-derived) alphabetizes legal names by moving
// a leading "The" to a trailing "(The)" suffix — e.g. "The Walt Disney
// Company" becomes "Walt Disney Company (The)". That's a sort key,
// not a display name, so we strip the suffix here.
//
// We also strip the legal-form suffix ("Inc.", "Corp.", "Company", etc.)
// from every name so all three sources (curated / sp500 / edgar) hit a
// single consistent display style: short brand-form names. Without this,
// the UI would render "Target Corporation" alongside "Cisco" alongside
// "Apple Inc." — three different conventions in the same row.
// `(?:&\s+)?Co...` so both " Co." and " & Company" strip in one match.
// Putting the `& Co...` form leftmost (via optional prefix) means
// "Wells Fargo & Company" matches at the space before "&" — leftmost
// wins in JS regex — and strips the whole " & Company" suffix, not
// just " Company" (which would leave a dangling "&").
const LEGAL_SUFFIX_RE =
  /,?\s+(?:Inc\.?|Incorporated|Corp(?:oration)?\.?|(?:&\s+)?Co(?:mpany|mpanies)?\.?|Ltd\.?|Limited|Holdings?|Group|LLC|PLC)$/i;

/** Iteratively strip chained legal suffixes from a display name.
 *  - "Apple Inc."                              → "Apple"
 *  - "Walmart Inc."                            → "Walmart"
 *  - "Cracker Barrel Old Country Store, Inc."  → "Cracker Barrel Old Country Store"
 *  - "Goldman Sachs Group, Inc."               → "Goldman Sachs"  (two passes: ", Inc." then " Group")
 *  - "PayPal Holdings, Inc."                   → "PayPal"         (two passes)
 *  - "Procter & Gamble Co."                    → "Procter & Gamble"
 *  - "JPMorgan Chase & Co."                    → "JPMorgan Chase"
 *  - "Linde plc"                               → "Linde"
 * International entity forms (S.A., N.V., A.G., S.p.A.) are deliberately
 * left in place — they're often part of the brand identity for non-US
 * issuers and the ticker alone is less unique. */
export function stripLegalSuffix(name: string): string {
  let prev: string;
  let current = name.trim();
  do {
    prev = current;
    current = current.replace(LEGAL_SUFFIX_RE, '').trim();
  } while (current !== prev && current.length > 0);
  return current;
}

function cleanSp500Name(raw: string): string {
  return stripLegalSuffix(
    raw
      // Drop trailing "(The)" sort suffix.
      .replace(/\s*\(The\)\s*$/i, '')
      // Share-class disclosure: "Alphabet Inc. (Class A)" → "Alphabet Inc.".
      // The ticker (GOOGL = A, GOOG = C, FOXA = A, FOX = B, NWSA = A,
      // NWS = B) already disambiguates classes — the parenthesized
      // tag is redundant and breaks the strip rule by sitting after
      // the legal suffix it would otherwise be matched against.
      .replace(/\s*\(Class [A-Z]\)\s*$/i, '')
      // Surname-first inversion: "Lilly (Eli)" → "Eli Lilly".
      .replace(/^(\S+)\s+\(([A-Z][a-z]+)\)\s*$/, '$2 $1')
      .trim(),
  );
}

const SP500_NAMES: Record<string, string> = Object.fromEntries(
  (sp500Constituents as { symbol: string; name: string }[]).map((c) => [
    c.symbol,
    cleanSp500Name(c.name),
  ]),
);

/** Hand-curated overrides for the handful of tickers where the
 *  suffix-stripped SP500 / EDGAR name comes out wrong. Every other
 *  ticker should rely on `stripLegalSuffix(SP500_NAMES | extendedNames)`
 *  so the entire UI uses one consistent "brand-form, no legal suffix"
 *  style ("Target", "Cisco Systems", "Walt Disney" — not "Target
 *  Corporation", "Cisco", "Disney").
 *
 *  Kept here only when:
 *   - EDGAR's casing produces a wrong result (PEP, JPM)
 *   - the brand has a non-word-suffix element that wouldn't strip
 *     cleanly (AMZN's ".com", "AMD" as an acronym brand) */
export const COMPANY_NAMES: Record<string, string> = {
  // sp500-constituents.json has "Nvidia" (lowercase i) but the brand is
  // all-caps "NVIDIA". sp500_NAMES wins over the EDGAR fallback, so this
  // override is the only way to render the brand form.
  NVDA: 'NVIDIA',
  // sp500-constituents.json has "Advanced Micro Devices" (full legal name).
  // Users know the acronym ticker as the brand — preserve it explicitly.
  AMD:  'AMD',
};

// ── EDGAR extended-names cache ─────────────────────────────────────────
// Fetched lazily from /public/ticker-names.json on first call. The JSON
// is built by scripts/build_ticker_names.mjs from SEC EDGAR's company
// registry (~10k US tickers, casing-normalized). Stored in module-level
// state so a single fetch serves every consumer; React components are
// notified via a subscribe/snapshot pair consumed by useEnsureCompanyNames.

let extendedNames: Record<string, string> = {};
let extendedNamesVersion = 0;
let extendedNamesPromise: Promise<void> | null = null;
const extendedSubscribers = new Set<() => void>();

/** Kicks off the EDGAR fetch on first call (idempotent — subsequent
 *  callers get the same in-flight promise). Returns the promise so
 *  callers can `await` if they need the data sync-style; most just
 *  fire-and-forget via the hook. Server-side calls return immediately
 *  (no window → no fetch). */
export function loadExtendedCompanyNames(): Promise<void> {
  if (extendedNamesPromise) return extendedNamesPromise;
  if (typeof window === 'undefined') return Promise.resolve();
  extendedNamesPromise = fetch('/ticker-names.json', { cache: 'force-cache' })
    .then((r) => (r.ok ? (r.json() as Promise<Record<string, string>>) : {}))
    .catch(() => ({}))
    .then((m) => {
      extendedNames = m ?? {};
      extendedNamesVersion += 1;
      for (const fn of extendedSubscribers) fn();
    });
  return extendedNamesPromise;
}

/** Subscription primitives for useSyncExternalStore. Internal — the
 *  hook in useCompanyNames.ts is the only intended consumer. */
export function subscribeToCompanyNames(cb: () => void): () => void {
  extendedSubscribers.add(cb);
  return () => {
    extendedSubscribers.delete(cb);
  };
}
export function getCompanyNamesVersion(): number {
  return extendedNamesVersion;
}

/** Returns the friendliest company name we have for `ticker`. Priority:
 *  1. Hand-curated COMPANY_NAMES override (most recognizable brand form)
 *  2. S&P 500 constituents JSON (covers ~500 names)
 *  3. SEC EDGAR fallback (covers ~10k US public companies, lazy-loaded)
 *  4. The ticker itself (graceful fallback while #3 is in flight or if
 *     the JSON 404s for any reason). */
export function companyName(ticker: string): string {
  return (
    COMPANY_NAMES[ticker] ??
    SP500_NAMES[ticker] ??
    extendedNames[ticker] ??
    ticker
  );
}
