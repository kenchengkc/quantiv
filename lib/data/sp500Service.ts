/**
 * S&P 500 Data Service
 * Provides canonical and optionally refreshed S&P 500 company data.
 */

// Dynamic S&P 500 list refresh interval (24h)
const DYNAMIC_SP500_TTL_MS = 24 * 60 * 60 * 1000;
// FMP endpoint for current S&P 500 constituents
const FMP_SP500_URL = 'https://financialmodelingprep.com/api/v3/sp500_constituent';

// S&P 500 companies data
export interface SP500Company {
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  marketCap?: number;
  // Exchange isn't present in the canonical CSV, so it's optional. Kept
  // in the type for the few hardcoded ETF entries below that still set it.
  exchange?: 'NYSE' | 'NASDAQ';
  founded?: number;
  employees?: number;
  website?: string;
}

// Canonical S&P 500 constituents (503 entries incl. GOOG/GOOGL, FOX/FOXA,
// NWS/NWSA dual classes). Source: lib/data/sp500-constituents.csv — refresh
// from https://en.wikipedia.org/wiki/List_of_S%26P_500_companies periodically.
import sp500Constituents from './sp500-constituents.json';

/** Clean Wikipedia-style sort artifacts AND legal-form suffixes out of
 *  company display names. Applied at every entry point so search,
 *  companyName lookup, and ticker pages all see the same normalized
 *  names regardless of source.
 *  - "Walt Disney Company (The)" → "Walt Disney"   (sort suffix + legal form)
 *  - "Lilly (Eli)"               → "Eli Lilly"    (surname-first inversion)
 *  - "Target Corporation"        → "Target"       (legal-form suffix)
 *  - "Cisco Systems, Inc."       → "Cisco Systems"
 *  Matches the strip rules in apps/frontend/lib/companyNames.ts so the
 *  search dropdown and the ticker pages can't drift apart on style. */
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
      // Share-class tag: "Alphabet Inc. (Class A)" → "Alphabet Inc."
      // (ticker already distinguishes share class; the tag is noise).
      .replace(/\s*\(Class [A-Z]\)\s*$/i, '')
      .replace(/^(\S+)\s+\(([A-Z][a-z]+)\)\s*$/, '$2 $1')
      .trim(),
  );
}

const SP500_COMPANIES: SP500Company[] = [
  ...(sp500Constituents as SP500Company[]).map((c) => ({
    ...c,
    name: normalizeDisplayName(c.name),
  })),

  // ETFs (popular S&P-tracking funds — not constituents but users still search them)
  { symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', sector: 'ETF', industry: 'Index Fund', exchange: 'NYSE' },
  { symbol: 'VOO', name: 'Vanguard S&P 500 ETF', sector: 'ETF', industry: 'Index Fund', exchange: 'NYSE' },
  { symbol: 'IVV', name: 'iShares Core S&P 500 ETF', sector: 'ETF', industry: 'Index Fund', exchange: 'NYSE' },
  { symbol: 'QQQ', name: 'Invesco QQQ Trust ETF', sector: 'ETF', industry: 'Tech Index Fund', exchange: 'NASDAQ' },
  { symbol: 'VTI', name: 'Vanguard Total Stock Market ETF', sector: 'ETF', industry: 'Total Market Fund', exchange: 'NYSE' },
];

class SP500DataService {
  private static instance: SP500DataService;
  private companies: Map<string, SP500Company> = new Map();
  // Dynamic cache for up-to-date S&P 500 list (server-only)
  private dynamicCompaniesCache: SP500Company[] | null = null;
  private dynamicFetchedAt = 0;
  private isFetchingDynamic = false;

  private constructor() {
    // Initialize companies map
    SP500_COMPANIES.forEach(company => {
      this.companies.set(company.symbol, company);
    });
  }

  public static getInstance(): SP500DataService {
    if (!SP500DataService.instance) {
      SP500DataService.instance = new SP500DataService();
    }
    return SP500DataService.instance;
  }

  /**
   * Server-only: fetch current S&P 500 constituents from FMP
   */
  private async fetchDynamicCompanies(): Promise<SP500Company[]> {
    try {
      if (typeof window !== 'undefined') {
        // Do not fetch on client
        return [];
      }

      const apiKey = process.env.FMP_API_KEY;
      if (!apiKey) return [];

      const url = `${FMP_SP500_URL}?apikey=${apiKey}`;
      const resp = await fetch(
        url,
        { next: { revalidate: 60 * 60 } } as RequestInit & { next: { revalidate: number } }
      );
      if (!resp.ok) return [];
      const data = await resp.json();

      if (!Array.isArray(data)) return [];

      // Map FMP fields to our shape; industry/exchange may be unknown here.
      // Names are normalized so dynamic data picks up the same display
      // rules as the static SP500_COMPANIES list (no "(The)" suffix, etc.).
      const mapped: SP500Company[] = data
        .filter((d: any) => typeof d?.symbol === 'string' && d.symbol.length > 0)
        .map((d: any) => ({
          symbol: d.symbol.toUpperCase(),
          name: normalizeDisplayName(
            (d.name || d.companyName || d.symbol).toString(),
          ),
          sector: (d.sector || 'Unknown').toString(),
          industry: (d.subSector || 'Unknown').toString(),
          exchange: 'NYSE',
        }));

      return mapped;
    } catch (err) {
      console.warn('[sp500Service] dynamic S&P 500 fetch failed:', err);
      return [];
    }
  }

  private async getDynamicCompanies(): Promise<SP500Company[] | null> {
    const now = Date.now();
    if (this.dynamicCompaniesCache && (now - this.dynamicFetchedAt) < DYNAMIC_SP500_TTL_MS) {
      return this.dynamicCompaniesCache;
    }

    if (this.isFetchingDynamic) return this.dynamicCompaniesCache;

    this.isFetchingDynamic = true;
    try {
      const companies = await this.fetchDynamicCompanies();
      if (companies.length > 0) {
        this.dynamicCompaniesCache = companies;
        this.dynamicFetchedAt = now;
        // Also seed the map for quick lookup
        companies.forEach((c) => this.companies.set(c.symbol, c));
        return companies;
      }
      return this.dynamicCompaniesCache; // could be null or previous value
    } finally {
      this.isFetchingDynamic = false;
    }
  }

  // Get all S&P 500 companies
  public getAllCompanies(): SP500Company[] {
    return SP500_COMPANIES;
  }

  // Async variant using dynamic list with fallback to static subset
  public async getAllCompaniesAsync(): Promise<SP500Company[]> {
    const dynamic = await this.getDynamicCompanies();
    return dynamic && dynamic.length > 0 ? dynamic : SP500_COMPANIES;
  }

  // Get company by symbol
  public getCompany(symbol: string): SP500Company | undefined {
    return this.companies.get(symbol.toUpperCase());
  }

  // Search companies by symbol or name
  public searchCompanies(query: string, limit: number = 10): SP500Company[] {
    const upperQuery = query.toUpperCase();
    const results: { company: SP500Company; score: number }[] = [];

    for (const company of SP500_COMPANIES) {
      let score = 0;
      
      // Exact symbol match gets highest score
      if (company.symbol === upperQuery) {
        score = 1000;
      }
      // Symbol starts with query
      else if (company.symbol.startsWith(upperQuery)) {
        score = 900;
      }
      // Symbol contains query
      else if (company.symbol.includes(upperQuery)) {
        score = 800;
      }
      // Company name starts with query (case insensitive)
      else if (company.name.toLowerCase().startsWith(query.toLowerCase())) {
        score = 700;
      }
      // Company name contains query (case insensitive)
      else if (company.name.toLowerCase().includes(query.toLowerCase())) {
        score = 600;
      }
      // Sector contains query
      else if (company.sector.toLowerCase().includes(query.toLowerCase())) {
        score = 500;
      }
      // Industry contains query
      else if (company.industry.toLowerCase().includes(query.toLowerCase())) {
        score = 400;
      }

      if (score > 0) {
        results.push({ company, score });
      }
    }

    return results
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map(result => result.company);
  }

  // Async variant using dynamic list with fallback
  public async searchCompaniesAsync(query: string, limit: number = 10): Promise<SP500Company[]> {
    const list = await this.getAllCompaniesAsync();
    const upperQuery = query.toUpperCase();
    const results: { company: SP500Company; score: number }[] = [];

    for (const company of list) {
      let score = 0;
      if (company.symbol === upperQuery) score = 1000;
      else if (company.symbol.startsWith(upperQuery)) score = 900;
      else if (company.symbol.includes(upperQuery)) score = 800;
      else if (company.name.toLowerCase().startsWith(query.toLowerCase())) score = 700;
      else if (company.name.toLowerCase().includes(query.toLowerCase())) score = 600;
      else if (company.sector.toLowerCase().includes(query.toLowerCase())) score = 500;
      else if (company.industry.toLowerCase().includes(query.toLowerCase())) score = 400;
      if (score > 0) results.push({ company, score });
    }

    return results
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      // Apply normalization on output too as a safety net — if a stale
      // singleton or upstream caller bypassed the import-time normalization,
      // the search response still gets clean names.
      .map((r) => ({ ...r.company, name: normalizeDisplayName(r.company.name) }));
  }

  // Get popular/most traded stocks
  public getPopularStocks(): SP500Company[] {
    const popularSymbols = [
      'SPY', 'QQQ', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 
      'NVDA', 'NFLX', 'JPM', 'V', 'UNH', 'HD', 'PG', 'JNJ', 'BAC', 'XOM'
    ];
    
    return popularSymbols
      .map(symbol => this.companies.get(symbol))
      .filter(company => company !== undefined) as SP500Company[];
  }

  // Async variant using dynamic list with fallback
  public async getPopularStocksAsync(): Promise<SP500Company[]> {
    const list = await this.getAllCompaniesAsync();
    const popularSymbols = [
      'SPY', 'QQQ', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 
      'NVDA', 'NFLX', 'JPM', 'V', 'UNH', 'HD', 'PG', 'JNJ', 'BAC', 'XOM'
    ];
    const map = new Map(list.map(c => [c.symbol, c] as const));
    return popularSymbols.map(s => map.get(s)).filter(Boolean) as SP500Company[];
  }

  // Get companies by sector
  public getCompaniesBySector(sector: string): SP500Company[] {
    return SP500_COMPANIES.filter(company => 
      company.sector.toLowerCase() === sector.toLowerCase()
    );
  }

  // Async variant using dynamic list with fallback
  public async getCompaniesBySectorAsync(sector: string): Promise<SP500Company[]> {
    const list = await this.getAllCompaniesAsync();
    return list.filter(c => c.sector.toLowerCase() === sector.toLowerCase());
  }

}

// Export singleton instance
export const sp500DataService = SP500DataService.getInstance();

// Utility functions
export function getAllSP500Companies(): SP500Company[] {
  return sp500DataService.getAllCompanies();
}

export function searchSP500Companies(query: string, limit?: number): SP500Company[] {
  return sp500DataService.searchCompanies(query, limit);
}

export function getPopularSP500Stocks(): SP500Company[] {
  return sp500DataService.getPopularStocks();
}

// Async utilities that prefer dynamic list
export async function getAllSP500CompaniesAsync(): Promise<SP500Company[]> {
  return await sp500DataService.getAllCompaniesAsync();
}

export async function searchSP500CompaniesAsync(query: string, limit?: number): Promise<SP500Company[]> {
  return await sp500DataService.searchCompaniesAsync(query, limit);
}

export async function getPopularSP500StocksAsync(): Promise<SP500Company[]> {
  return await sp500DataService.getPopularStocksAsync();
}
