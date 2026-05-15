import sp500Constituents from '../../../lib/data/sp500-constituents.json';

// 503-row S&P 500 lookup (symbol → company name). Used as the fallback
// when the hand-curated COMPANY_NAMES map below doesn't have an entry,
// so tickers like EA, JBL, KMX render their real company names on the
// screener / detail / watchlist instead of just echoing the symbol.
//
// The source list (Wikipedia-derived) alphabetizes legal names by moving
// a leading "The" to a trailing "(The)" suffix — e.g. "The Walt Disney
// Company" becomes "Walt Disney Company (The)". That's a sort key,
// not a display name, so we strip the suffix here. Hand-curated entries
// in COMPANY_NAMES still win for the tickers we want shorter / branded
// forms for (DIS → "Disney", KO → "Coca-Cola").
const SP500_NAMES: Record<string, string> = Object.fromEntries(
  (sp500Constituents as { symbol: string; name: string }[]).map((c) => [
    c.symbol,
    c.name.replace(/\s*\(The\)\s*$/i, '').trim(),
  ]),
);

/** Hand-curated overrides — shorter / more recognizable brand names than
 *  the sp500 dataset gives (e.g. "Apple Inc." instead of just "Apple",
 *  "AT&T" instead of "AT&T Inc."). Consulted before SP500_NAMES. */
export const COMPANY_NAMES: Record<string, string> = {
  AAPL: 'Apple Inc.',
  MSFT: 'Microsoft',
  AMZN: 'Amazon',
  NVDA: 'NVIDIA',
  GOOGL: 'Alphabet',
  META: 'Meta Platforms',
  TSLA: 'Tesla',
  NFLX: 'Netflix',
  JPM: 'JPMorgan Chase',
  V: 'Visa',
  MA: 'Mastercard',
  WFC: 'Wells Fargo',
  GS: 'Goldman Sachs',
  BAC: 'Bank of America',
  UNH: 'UnitedHealth',
  LLY: 'Eli Lilly',
  PFE: 'Pfizer',
  ABBV: 'AbbVie',
  BMY: 'Bristol-Myers',
  GILD: 'Gilead',
  HUM: 'Humana',
  XOM: 'Exxon Mobil',
  CVX: 'Chevron',
  COP: 'ConocoPhillips',
  AMD: 'AMD',
  AVGO: 'Broadcom',
  CRM: 'Salesforce',
  ADBE: 'Adobe',
  INTC: 'Intel',
  QCOM: 'Qualcomm',
  CSCO: 'Cisco',
  COIN: 'Coinbase',
  SNOW: 'Snowflake',
  PLTR: 'Palantir',
  HD: 'Home Depot',
  MCD: "McDonald's",
  SBUX: 'Starbucks',
  NKE: 'Nike',
  KO: 'Coca-Cola',
  PEP: 'PepsiCo',
  DIS: 'Disney',
  BA: 'Boeing',
  CAT: 'Caterpillar',
  LIN: 'Linde',
  T: 'AT&T',
  VZ: 'Verizon',
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
