// Stock lookup over the canonical S&P 500 constituents JSON plus a small
// ETF list. Pure in-memory; no runtime fetches. The /api/stocks/search
// route is the only consumer.

import sp500Constituents from './sp500-constituents.json';

export interface Stock {
  symbol: string;
  name: string;
  sector?: string;
  exchange?: 'NYSE' | 'NASDAQ';
}

const LEGAL_SUFFIX_RE =
  /,?\s+(?:Inc\.?|Incorporated|Corp(?:oration)?\.?|(?:&\s+)?Co(?:mpany|mpanies)?\.?|Ltd\.?|Limited|Holdings?|Group|LLC|PLC)$/i;

function stripLegalSuffix(name: string): string {
  let prev: string;
  let current = name.trim();
  do {
    prev = current;
    current = current.replace(LEGAL_SUFFIX_RE, '').trim();
  } while (current !== prev && current.length > 0);
  return current;
}

function normalizeDisplayName(name: string): string {
  return stripLegalSuffix(
    name
      .replace(/\s*\(The\)\s*$/i, '')
      .replace(/\s*\(Class [A-Z]\)\s*$/i, '')
      .replace(/^(\S+)\s+\(([A-Z][a-z]+)\)\s*$/, '$2 $1')
      .trim(),
  );
}

const ETF_OVERRIDES: Stock[] = [
  { symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', sector: 'ETF', exchange: 'NYSE' },
  { symbol: 'VOO', name: 'Vanguard S&P 500 ETF', sector: 'ETF', exchange: 'NYSE' },
  { symbol: 'IVV', name: 'iShares Core S&P 500 ETF', sector: 'ETF', exchange: 'NYSE' },
  { symbol: 'QQQ', name: 'Invesco QQQ Trust ETF', sector: 'ETF', exchange: 'NASDAQ' },
  { symbol: 'VTI', name: 'Vanguard Total Stock Market ETF', sector: 'ETF', exchange: 'NYSE' },
];

const STOCKS: Stock[] = [
  ...(sp500Constituents as { symbol: string; name: string; sector: string }[]).map((c) => ({
    symbol: c.symbol,
    name: normalizeDisplayName(c.name),
    sector: c.sector,
  })),
  ...ETF_OVERRIDES,
];

// Ranked symbol/name match. Returns at most `limit` results.
export function searchStocks(query: string, limit = 10): Stock[] {
  const upper = query.toUpperCase();
  const lower = query.toLowerCase();
  const scored: { stock: Stock; score: number }[] = [];

  for (const stock of STOCKS) {
    let score = 0;
    if (stock.symbol === upper) score = 1000;
    else if (stock.symbol.startsWith(upper)) score = 900;
    else if (stock.symbol.includes(upper)) score = 800;
    else if (stock.name.toLowerCase().startsWith(lower)) score = 700;
    else if (stock.name.toLowerCase().includes(lower)) score = 600;
    else if (stock.sector?.toLowerCase().includes(lower)) score = 500;
    if (score > 0) scored.push({ stock, score });
  }

  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((r) => r.stock);
}
