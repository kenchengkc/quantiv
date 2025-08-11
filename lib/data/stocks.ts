// Stock database interface - now powered by real S&P 500 data
import { 
  sp500DataService, 
  getAllSP500Companies, 
  searchSP500Companies, 
  getPopularSP500Stocks, 
  type SP500Company 
} from './sp500Service';

export interface Stock {
  symbol: string;
  name: string;
  sector?: string;
  exchange?: 'NYSE' | 'NASDAQ';
}

// Convert SP500Company to Stock interface for backward compatibility
function convertSP500ToStock(company: SP500Company): Stock {
  return {
    symbol: company.symbol,
    name: company.name,
    sector: company.sector,
    exchange: company.exchange
  };
}

// Get all S&P 500 companies as stocks
export function getAllStocks(): Stock[] {
  return getAllSP500Companies().map(convertSP500ToStock);
}

// Search stocks using S&P 500 data
export function searchStocks(query: string, limit: number = 10): Stock[] {
  return searchSP500Companies(query, limit).map(convertSP500ToStock);
}

// Get popular stocks using S&P 500 data
export function getPopularStocks(): Stock[] {
  return getPopularSP500Stocks().map(convertSP500ToStock);
}

// Legacy export for backward compatibility - now uses real S&P 500 data
export const STOCKS_DATABASE: Stock[] = getAllStocks();

// Export individual popular stocks for quick access
export const POPULAR_STOCKS = getPopularStocks();

// Export by sector for filtering
export function getStocksBySector(sector: string): Stock[] {
  return sp500DataService.getCompaniesBySector(sector).map(convertSP500ToStock);
}

// Get stock by symbol
export function getStockBySymbol(symbol: string): Stock | undefined {
  const company = sp500DataService.getCompany(symbol);
  return company ? convertSP500ToStock(company) : undefined;
}
