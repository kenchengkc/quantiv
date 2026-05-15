import sp500Constituents from '../../../lib/data/sp500-constituents.json';

// 503-row S&P 500 lookup (symbol → company name). Used as the fallback
// when the hand-curated COMPANY_NAMES map below doesn't have an entry,
// so tickers like EA, JBL, KMX render their real company names on the
// screener / detail / watchlist instead of just echoing the symbol.
const SP500_NAMES: Record<string, string> = Object.fromEntries(
  (sp500Constituents as { symbol: string; name: string }[]).map((c) => [
    c.symbol,
    c.name,
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

/** Returns the friendliest company name we have for `ticker`. Priority:
 *  1. Hand-curated COMPANY_NAMES override (most recognizable brand form)
 *  2. S&P 500 constituents JSON (covers ~500 names)
 *  3. The ticker itself (graceful fallback) */
export function companyName(ticker: string): string {
  return COMPANY_NAMES[ticker] ?? SP500_NAMES[ticker] ?? ticker;
}
