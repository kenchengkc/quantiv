/**
 * Polygon.io API Service for Options Chain Data
 * Handles options contracts, strikes, and Greeks
 */

export interface PolygonOptionContract {
  strike: number;
  bid: number;
  ask: number;
  mid: number;
  last: number;
  volume: number;
  openInterest: number;
  impliedVolatility: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  inTheMoney: boolean;
}

export interface PolygonOptionsChain {
  symbol: string;
  expirationDate: string;
  daysToExpiry: number;
  underlyingPrice: number;
  strikes: {
    calls: Record<string, PolygonOptionContract>;
    puts: Record<string, PolygonOptionContract>;
  };
  dataSource: 'polygon';
}

class PolygonService {
  private static instance: PolygonService;
  private apiKey: string;
  private baseUrl = 'https://api.polygon.io';

  private constructor() {
    this.apiKey = process.env.POLYGON_API_KEY || '';
  }

  public static getInstance(): PolygonService {
    if (!PolygonService.instance) {
      PolygonService.instance = new PolygonService();
    }
    return PolygonService.instance;
  }

  public isAvailable(): boolean {
    return !!this.apiKey;
  }

  /**
   * Fetch options chain data from Polygon
   */
  public async fetchOptionsChain(symbol: string, expiration?: string): Promise<PolygonOptionsChain | null> {
    if (!this.isAvailable()) {
      console.warn('[Polygon] API key not configured');
      return null;
    }

    try {
      // Get underlying price first
      const underlyingPrice = await this.fetchUnderlyingPrice(symbol);
      
      // If no specific expiration provided, get the next available one
      let targetExpiration = expiration;
      if (!targetExpiration) {
        const expirations = await this.fetchExpirationDates(symbol);
        if (expirations.length === 0) {
          console.warn(`[Polygon] No options expirations found for ${symbol}`);
          return null;
        }
        targetExpiration = expirations[0];
      }

      // Fetch options contracts for the expiration
      const optionsResponse = await fetch(
        `${this.baseUrl}/v3/reference/options/contracts?underlying_ticker=${symbol}&expiration_date=${targetExpiration}&limit=1000&apikey=${this.apiKey}`
      );

      if (!optionsResponse.ok) {
        if (optionsResponse.status === 429) {
          throw new Error('POLYGON_RATE_LIMITED');
        }
        throw new Error(`Polygon options API error: ${optionsResponse.status}`);
      }

      const contractsData = await optionsResponse.json();
      
      if (!contractsData.results || contractsData.results.length === 0) {
        console.warn(`[Polygon] No options contracts found for ${symbol} expiring ${targetExpiration}`);
        return null;
      }

      // Process contracts into calls and puts
      const calls: Record<string, PolygonOptionContract> = {};
      const puts: Record<string, PolygonOptionContract> = {};

      // Get market data for each contract
      for (const contract of contractsData.results) {
        try {
          const marketData = await this.fetchContractMarketData(contract.ticker);
          if (!marketData) continue;

          const optionContract: PolygonOptionContract = {
            strike: contract.strike_price,
            bid: marketData.bid || 0,
            ask: marketData.ask || 0,
            mid: marketData.bid && marketData.ask ? (marketData.bid + marketData.ask) / 2 : marketData.last || 0,
            last: marketData.last || 0,
            volume: marketData.volume || 0,
            openInterest: marketData.open_interest || 0,
            impliedVolatility: marketData.implied_volatility || 0,
            delta: marketData.delta || 0,
            gamma: marketData.gamma || 0,
            theta: marketData.theta || 0,
            vega: marketData.vega || 0,
            inTheMoney: contract.contract_type === 'call' 
              ? contract.strike_price < underlyingPrice 
              : contract.strike_price > underlyingPrice
          };

          if (contract.contract_type === 'call') {
            calls[`${contract.strike_price}C`] = optionContract;
          } else if (contract.contract_type === 'put') {
            puts[`${contract.strike_price}P`] = optionContract;
          }
        } catch (error) {
          console.warn(`[Polygon] Failed to fetch market data for ${contract.ticker}:`, error);
          continue;
        }
      }

      // Calculate days to expiry
      const expiryDate = new Date(targetExpiration);
      const today = new Date();
      const daysToExpiry = Math.max(1, Math.ceil((expiryDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24)));

      return {
        symbol,
        expirationDate: targetExpiration,
        daysToExpiry,
        underlyingPrice,
        strikes: {
          calls,
          puts
        },
        dataSource: 'polygon'
      };
    } catch (error) {
      console.error(`[Polygon] Options chain fetch failed for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Fetch available expiration dates for a symbol
   */
  private async fetchExpirationDates(symbol: string): Promise<string[]> {
    try {
      const response = await fetch(
        `${this.baseUrl}/v3/reference/options/contracts?underlying_ticker=${symbol}&limit=1000&apikey=${this.apiKey}`
      );

      if (!response.ok) {
        throw new Error(`Polygon expirations API error: ${response.status}`);
      }

      const data = await response.json();
      
      if (!data.results) {
        return [];
      }

      // Extract unique expiration dates and sort them
      const allExpirations = data.results.map((contract: any) => contract.expiration_date as string);
      const uniqueExpirations = [...new Set(allExpirations)];
      const expirations = (uniqueExpirations as string[])
        .sort()
        .filter((date: string) => new Date(date) > new Date()); // Only future expirations

      return expirations;
    } catch (error) {
      console.error(`[Polygon] Failed to fetch expirations for ${symbol}:`, error);
      return [];
    }
  }

  /**
   * Fetch underlying stock price
   */
  private async fetchUnderlyingPrice(symbol: string): Promise<number> {
    try {
      const response = await fetch(
        `${this.baseUrl}/v2/aggs/ticker/${symbol}/prev?adjusted=true&apikey=${this.apiKey}`
      );

      if (!response.ok) {
        throw new Error(`Polygon price API error: ${response.status}`);
      }

      const data = await response.json();
      
      if (data.results && data.results.length > 0) {
        return data.results[0].c; // Close price
      }

      return 0;
    } catch (error) {
      console.error(`[Polygon] Failed to fetch price for ${symbol}:`, error);
      return 0;
    }
  }

  /**
   * Fetch market data for a specific options contract
   */
  private async fetchContractMarketData(contractTicker: string): Promise<any> {
    try {
      const response = await fetch(
        `${this.baseUrl}/v3/snapshot/options/${contractTicker}?apikey=${this.apiKey}`
      );

      if (!response.ok) {
        return null;
      }

      const data = await response.json();
      return data.results;
    } catch (error) {
      console.warn(`[Polygon] Failed to fetch market data for ${contractTicker}:`, error);
      return null;
    }
  }
}

export const polygonService = PolygonService.getInstance();
