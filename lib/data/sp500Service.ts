/**
 * S&P 500 Data Service
 * Provides real S&P 500 company data and integrates with Yahoo Finance for live market data
 */

// Note: Do NOT import 'yahoo-finance2' at top-level to avoid bundling it into client code.
// We'll dynamically import it inside server-only methods when needed.

// Dynamic S&P 500 list refresh interval (24h)
const DYNAMIC_SP500_TTL_MS = 24 * 60 * 60 * 1000;
// FMP endpoint for current S&P 500 constituents
const FMP_SP500_URL = 'https://financialmodelingprep.com/api/v3/sp500_constituent';

// S&P 500 companies data (real companies, not hardcoded)
export interface SP500Company {
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  marketCap?: number;
  exchange: 'NYSE' | 'NASDAQ';
  founded?: number;
  employees?: number;
  website?: string;
}

// Real S&P 500 companies (subset for initial implementation)
const SP500_COMPANIES: SP500Company[] = [
  // Technology
  { symbol: 'AAPL', name: 'Apple Inc.', sector: 'Technology', industry: 'Consumer Electronics', exchange: 'NASDAQ' },
  { symbol: 'MSFT', name: 'Microsoft Corporation', sector: 'Technology', industry: 'Software', exchange: 'NASDAQ' },
  { symbol: 'GOOGL', name: 'Alphabet Inc. Class A', sector: 'Technology', industry: 'Internet Services', exchange: 'NASDAQ' },
  { symbol: 'GOOG', name: 'Alphabet Inc. Class C', sector: 'Technology', industry: 'Internet Services', exchange: 'NASDAQ' },
  { symbol: 'AMZN', name: 'Amazon.com Inc.', sector: 'Consumer Discretionary', industry: 'E-commerce', exchange: 'NASDAQ' },
  { symbol: 'TSLA', name: 'Tesla Inc.', sector: 'Consumer Discretionary', industry: 'Electric Vehicles', exchange: 'NASDAQ' },
  { symbol: 'META', name: 'Meta Platforms Inc.', sector: 'Technology', industry: 'Social Media', exchange: 'NASDAQ' },
  { symbol: 'NVDA', name: 'NVIDIA Corporation', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'NFLX', name: 'Netflix Inc.', sector: 'Communication Services', industry: 'Streaming', exchange: 'NASDAQ' },
  { symbol: 'AMD', name: 'Advanced Micro Devices Inc.', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'CRM', name: 'Salesforce Inc.', sector: 'Technology', industry: 'Cloud Software', exchange: 'NYSE' },
  { symbol: 'ORCL', name: 'Oracle Corporation', sector: 'Technology', industry: 'Database Software', exchange: 'NYSE' },
  { symbol: 'ADBE', name: 'Adobe Inc.', sector: 'Technology', industry: 'Software', exchange: 'NASDAQ' },
  { symbol: 'INTC', name: 'Intel Corporation', sector: 'Technology', industry: 'Semiconductors', exchange: 'NASDAQ' },
  { symbol: 'CSCO', name: 'Cisco Systems Inc.', sector: 'Technology', industry: 'Networking', exchange: 'NASDAQ' },
  
  // Financial Services
  { symbol: 'JPM', name: 'JPMorgan Chase & Co.', sector: 'Financial Services', industry: 'Banking', exchange: 'NYSE' },
  { symbol: 'BAC', name: 'Bank of America Corporation', sector: 'Financial Services', industry: 'Banking', exchange: 'NYSE' },
  { symbol: 'WFC', name: 'Wells Fargo & Company', sector: 'Financial Services', industry: 'Banking', exchange: 'NYSE' },
  { symbol: 'GS', name: 'Goldman Sachs Group Inc.', sector: 'Financial Services', industry: 'Investment Banking', exchange: 'NYSE' },
  { symbol: 'MS', name: 'Morgan Stanley', sector: 'Financial Services', industry: 'Investment Banking', exchange: 'NYSE' },
  { symbol: 'V', name: 'Visa Inc.', sector: 'Financial Services', industry: 'Payment Processing', exchange: 'NYSE' },
  { symbol: 'MA', name: 'Mastercard Incorporated', sector: 'Financial Services', industry: 'Payment Processing', exchange: 'NYSE' },
  { symbol: 'PYPL', name: 'PayPal Holdings Inc.', sector: 'Financial Services', industry: 'Digital Payments', exchange: 'NASDAQ' },
  { symbol: 'AXP', name: 'American Express Company', sector: 'Financial Services', industry: 'Credit Services', exchange: 'NYSE' },
  { symbol: 'BLK', name: 'BlackRock Inc.', sector: 'Financial Services', industry: 'Asset Management', exchange: 'NYSE' },
  
  // Healthcare
  { symbol: 'UNH', name: 'UnitedHealth Group Incorporated', sector: 'Healthcare', industry: 'Health Insurance', exchange: 'NYSE' },
  { symbol: 'JNJ', name: 'Johnson & Johnson', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'PFE', name: 'Pfizer Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'ABBV', name: 'AbbVie Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'TMO', name: 'Thermo Fisher Scientific Inc.', sector: 'Healthcare', industry: 'Life Sciences', exchange: 'NYSE' },
  { symbol: 'ABT', name: 'Abbott Laboratories', sector: 'Healthcare', industry: 'Medical Devices', exchange: 'NYSE' },
  { symbol: 'CVS', name: 'CVS Health Corporation', sector: 'Healthcare', industry: 'Healthcare Services', exchange: 'NYSE' },
  { symbol: 'LLY', name: 'Eli Lilly and Company', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'MRK', name: 'Merck & Co. Inc.', sector: 'Healthcare', industry: 'Pharmaceuticals', exchange: 'NYSE' },
  { symbol: 'MDT', name: 'Medtronic plc', sector: 'Healthcare', industry: 'Medical Devices', exchange: 'NYSE' },
  
  // Consumer Discretionary
  { symbol: 'HD', name: 'Home Depot Inc.', sector: 'Consumer Discretionary', industry: 'Home Improvement', exchange: 'NYSE' },
  { symbol: 'MCD', name: 'McDonald\'s Corporation', sector: 'Consumer Discretionary', industry: 'Restaurants', exchange: 'NYSE' },
  { symbol: 'DIS', name: 'Walt Disney Company', sector: 'Communication Services', industry: 'Entertainment', exchange: 'NYSE' },
  { symbol: 'NKE', name: 'Nike Inc.', sector: 'Consumer Discretionary', industry: 'Footwear', exchange: 'NYSE' },
  { symbol: 'SBUX', name: 'Starbucks Corporation', sector: 'Consumer Discretionary', industry: 'Restaurants', exchange: 'NASDAQ' },
  { symbol: 'LOW', name: 'Lowe\'s Companies Inc.', sector: 'Consumer Discretionary', industry: 'Home Improvement', exchange: 'NYSE' },
  { symbol: 'TGT', name: 'Target Corporation', sector: 'Consumer Discretionary', industry: 'Retail', exchange: 'NYSE' },
  { symbol: 'BKNG', name: 'Booking Holdings Inc.', sector: 'Consumer Discretionary', industry: 'Travel', exchange: 'NASDAQ' },
  
  // Consumer Staples
  { symbol: 'WMT', name: 'Walmart Inc.', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NYSE' },
  { symbol: 'PG', name: 'Procter & Gamble Company', sector: 'Consumer Staples', industry: 'Personal Care', exchange: 'NYSE' },
  { symbol: 'KO', name: 'Coca-Cola Company', sector: 'Consumer Staples', industry: 'Beverages', exchange: 'NYSE' },
  { symbol: 'PEP', name: 'PepsiCo Inc.', sector: 'Consumer Staples', industry: 'Beverages', exchange: 'NASDAQ' },
  { symbol: 'COST', name: 'Costco Wholesale Corporation', sector: 'Consumer Staples', industry: 'Retail', exchange: 'NASDAQ' },
  { symbol: 'WBA', name: 'Walgreens Boots Alliance Inc.', sector: 'Consumer Staples', industry: 'Pharmacy', exchange: 'NASDAQ' },
  
  // Energy
  { symbol: 'XOM', name: 'Exxon Mobil Corporation', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  { symbol: 'CVX', name: 'Chevron Corporation', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  { symbol: 'COP', name: 'ConocoPhillips', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  { symbol: 'EOG', name: 'EOG Resources Inc.', sector: 'Energy', industry: 'Oil & Gas', exchange: 'NYSE' },
  
  // Industrials
  { symbol: 'BA', name: 'Boeing Company', sector: 'Industrials', industry: 'Aerospace', exchange: 'NYSE' },
  { symbol: 'CAT', name: 'Caterpillar Inc.', sector: 'Industrials', industry: 'Heavy Machinery', exchange: 'NYSE' },
  { symbol: 'GE', name: 'General Electric Company', sector: 'Industrials', industry: 'Conglomerate', exchange: 'NYSE' },
  { symbol: 'MMM', name: '3M Company', sector: 'Industrials', industry: 'Diversified Manufacturing', exchange: 'NYSE' },
  { symbol: 'UPS', name: 'United Parcel Service Inc.', sector: 'Industrials', industry: 'Logistics', exchange: 'NYSE' },
  { symbol: 'HON', name: 'Honeywell International Inc.', sector: 'Industrials', industry: 'Aerospace', exchange: 'NASDAQ' },
  
  // Utilities
  { symbol: 'NEE', name: 'NextEra Energy Inc.', sector: 'Utilities', industry: 'Electric Utilities', exchange: 'NYSE' },
  { symbol: 'DUK', name: 'Duke Energy Corporation', sector: 'Utilities', industry: 'Electric Utilities', exchange: 'NYSE' },
  { symbol: 'SO', name: 'Southern Company', sector: 'Utilities', industry: 'Electric Utilities', exchange: 'NYSE' },
  
  // Real Estate
  { symbol: 'AMT', name: 'American Tower Corporation', sector: 'Real Estate', industry: 'REITs', exchange: 'NYSE' },
  { symbol: 'PLD', name: 'Prologis Inc.', sector: 'Real Estate', industry: 'REITs', exchange: 'NYSE' },
  { symbol: 'CCI', name: 'Crown Castle Inc.', sector: 'Real Estate', industry: 'REITs', exchange: 'NYSE' },
  
  // Materials
  { symbol: 'LIN', name: 'Linde plc', sector: 'Materials', industry: 'Chemicals', exchange: 'NYSE' },
  { symbol: 'APD', name: 'Air Products and Chemicals Inc.', sector: 'Materials', industry: 'Chemicals', exchange: 'NYSE' },
  { symbol: 'SHW', name: 'Sherwin-Williams Company', sector: 'Materials', industry: 'Chemicals', exchange: 'NYSE' },
  
  // ETFs (Popular ones that track S&P 500)
  { symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', sector: 'ETF', industry: 'Index Fund', exchange: 'NYSE' },
  { symbol: 'VOO', name: 'Vanguard S&P 500 ETF', sector: 'ETF', industry: 'Index Fund', exchange: 'NYSE' },
  { symbol: 'IVV', name: 'iShares Core S&P 500 ETF', sector: 'ETF', industry: 'Index Fund', exchange: 'NYSE' },
  { symbol: 'QQQ', name: 'Invesco QQQ Trust ETF', sector: 'ETF', industry: 'Tech Index Fund', exchange: 'NASDAQ' },
  { symbol: 'VTI', name: 'Vanguard Total Stock Market ETF', sector: 'ETF', industry: 'Total Market Fund', exchange: 'NYSE' },
];

export interface LiveQuoteData {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap?: number;
  pe?: number;
  high52Week?: number;
  low52Week?: number;
  previousClose?: number;
  dayHigh?: number;
  dayLow?: number;
  avgVolume?: number;
  timestamp: string;
}

export interface LiveOptionsData {
  symbol: string;
  expirationDate: string;
  strikes: Array<{
    strike: number;
    call: {
      bid: number;
      ask: number;
      last: number;
      volume: number;
      openInterest: number;
      impliedVolatility: number;
      delta?: number;
      gamma?: number;
      theta?: number;
      vega?: number;
    };
    put: {
      bid: number;
      ask: number;
      last: number;
      volume: number;
      openInterest: number;
      impliedVolatility: number;
      delta?: number;
      gamma?: number;
      theta?: number;
      vega?: number;
    };
  }>;
}

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
      const resp = await fetch(url, { next: { revalidate: 60 * 60 } });
      if (!resp.ok) return [];
      const data = await resp.json();

      if (!Array.isArray(data)) return [];

      // Map FMP fields to our shape; industry/exchange may be unknown here
      const mapped: SP500Company[] = data
        .filter((d: any) => typeof d?.symbol === 'string' && d.symbol.length > 0)
        .map((d: any) => ({
          symbol: d.symbol.toUpperCase(),
          name: (d.name || d.companyName || d.symbol).toString(),
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
      .map(r => r.company);
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

  // Fetch live quote data using Yahoo Finance
  public async fetchLiveQuote(symbol: string): Promise<LiveQuoteData | null> {
    try {
      // Guard: this method is server-only to avoid shipping yahoo-finance2 to the browser
      if (typeof window !== 'undefined') {
        console.warn('[sp500Service] fetchLiveQuote called on the client; returning null');
        return null;
      }

      const yahooFinance = (await import('yahoo-finance2')).default;
      const quote = await yahooFinance.quote(symbol);
      const company = this.getCompany(symbol);
      
      if (!quote || !quote.regularMarketPrice) {
        return null;
      }

      return {
        symbol: quote.symbol || symbol,
        name: company?.name || quote.longName || quote.shortName || `${symbol} Company`,
        price: quote.regularMarketPrice,
        change: quote.regularMarketChange || 0,
        changePercent: quote.regularMarketChangePercent || 0,
        volume: quote.regularMarketVolume || 0,
        marketCap: quote.marketCap,
        pe: quote.trailingPE,
        high52Week: quote.fiftyTwoWeekHigh,
        low52Week: quote.fiftyTwoWeekLow,
        previousClose: quote.regularMarketPreviousClose,
        dayHigh: quote.regularMarketDayHigh,
        dayLow: quote.regularMarketDayLow,
        avgVolume: quote.averageDailyVolume3Month,
        timestamp: new Date().toISOString()
      };
    } catch (error) {
      console.error(`Failed to fetch live quote for ${symbol}:`, error);
      return null;
    }
  }

  // Fetch multiple quotes at once
  public async fetchMultipleQuotes(symbols: string[]): Promise<Map<string, LiveQuoteData>> {
    const quotes = new Map<string, LiveQuoteData>();
    
    try {
      const results = await Promise.allSettled(
        symbols.map(symbol => this.fetchLiveQuote(symbol))
      );

      results.forEach((result, index) => {
        if (result.status === 'fulfilled' && result.value) {
          quotes.set(symbols[index], result.value);
        }
      });
    } catch (error) {
      console.error('Failed to fetch multiple quotes:', error);
    }

    return quotes;
  }

  // All mock data generation methods removed - using only live API data
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

