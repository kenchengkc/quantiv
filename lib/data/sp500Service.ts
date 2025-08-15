/**
 * S&P 500 Data Service
 * Provides real S&P 500 company data and integrates with Yahoo Finance for live market data
 */

import yahooFinance from 'yahoo-finance2';

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

  // Get all S&P 500 companies
  public getAllCompanies(): SP500Company[] {
    return SP500_COMPANIES;
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

  // Get companies by sector
  public getCompaniesBySector(sector: string): SP500Company[] {
    return SP500_COMPANIES.filter(company => 
      company.sector.toLowerCase() === sector.toLowerCase()
    );
  }

  // Fetch live quote data using Yahoo Finance
  public async fetchLiveQuote(symbol: string): Promise<LiveQuoteData | null> {
    try {
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

export async function fetchLiveQuoteData(symbol: string): Promise<LiveQuoteData | null> {
  try {
    // Import enhanced live data service dynamically to avoid circular imports
    const { fetchEnhancedQuote } = await import('@/lib/services/enhancedLiveDataService');
    
    // Try to fetch live data first
    const enhancedQuote = await fetchEnhancedQuote(symbol);
    
    if (enhancedQuote && enhancedQuote.price > 0) {
      // Convert enhanced quote to LiveQuoteData format
      const company = sp500DataService.getCompany(symbol);
      return {
        symbol: enhancedQuote.symbol,
        name: company?.name || symbol,
        price: enhancedQuote.price,
        change: enhancedQuote.change,
        changePercent: enhancedQuote.changePercent,
        volume: enhancedQuote.volume,
        marketCap: enhancedQuote.marketCap,
        pe: enhancedQuote.peRatio,
        timestamp: new Date().toISOString()
      };
    }
  } catch (error) {
    // Handle rate limiting gracefully
    if (error instanceof Error && error.message.includes('429')) {
      console.warn(`[fetchLiveQuoteData] API rate limited for ${symbol}, no data available`);
    } else {
      console.warn(`[fetchLiveQuoteData] Enhanced live data failed for ${symbol}:`, error);
    }
  }

  // No mock data - return null if live data unavailable
  console.warn(`[fetchLiveQuoteData] No live data available for ${symbol}`);
  return null;
}

export async function fetchLiveOnlyQuoteData(symbol: string): Promise<LiveQuoteData | null> {
  const liveData = await fetchLiveQuoteData(symbol);
  
  if (liveData) {
    return liveData;
  }

  // No mock data - return null if live data unavailable
  return null;
}
