/**
 * Financial Modeling Prep (FMP) API Service
 * Provides quotes and earnings data only
 * Note: Options data is handled by Polygon service
 */

export interface FMPQuoteData {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap: number;
  pe: number;
  high52Week: number;
  low52Week: number;
  timestamp: string;
}



export interface FMPEarningsData {
  symbol: string;
  nextEarningsDate?: string | null;
  nextEarningsTime?: 'BMO' | 'AMC' | 'UNKNOWN';
  estimatedEPS?: number | null;
  estimatedRevenue?: number | null;
  historicalEarnings: Array<{
    date: string;
    actualEPS: number | null;
    estimatedEPS: number | null;
    actualRevenue: number | null;
    estimatedRevenue: number | null;
    epsSurprise: number | null;
    epsSurprisePercent: number | null;
    revenueSurprise: number | null;
    revenueSurprisePercent: number | null;
    priceMoveBefore: number;
    priceMoveAfter: number;
    priceMovePercent: number;
  }>;
  upcomingEarnings?: Array<{
    date: string;
    actualEPS: null;
    estimatedEPS: number | null;
    actualRevenue: null;
    estimatedRevenue: number | null;
    epsSurprise: null;
    epsSurprisePercent: null;
    revenueSurprise: null;
    revenueSurprisePercent: null;
    priceMoveBefore: number;
    priceMoveAfter: number;
    priceMovePercent: number;
  }>;
  stats: {
    avgMove: number;
    avgAbsMove: number;
    beatRate: number;
    avgBeat: number;
    revenueBeatRate: number;
    avgRevenueBeat: number;
  };
  dataSource: 'fmp';
}

class FMPService {
  private static instance: FMPService;
  private apiKey: string;
  private baseUrl = 'https://financialmodelingprep.com/api/v3';

  private constructor() {
    this.apiKey = process.env.FMP_API_KEY || '';
  }

  public static getInstance(): FMPService {
    if (!FMPService.instance) {
      FMPService.instance = new FMPService();
    }
    return FMPService.instance;
  }

  public isAvailable(): boolean {
    return !!this.apiKey;
  }

  /**
   * Fetch real-time quote data
   */
  public async fetchQuote(symbol: string): Promise<FMPQuoteData | null> {
    if (!this.isAvailable()) {
      console.warn('[FMP] API key not configured');
      return null;
    }

    try {
      const response = await fetch(
        `${this.baseUrl}/quote/${symbol}?apikey=${this.apiKey}`
      );

      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('FMP_RATE_LIMITED');
        }
        throw new Error(`FMP API error: ${response.status}`);
      }

      const data = await response.json();
      
      if (!data || data.length === 0) {
        return null;
      }

      const quote = data[0];
      console.log(`[FMP] Raw quote data for ${symbol}:`, quote);
      
      return {
        symbol: quote.symbol,
        name: quote.name || `${symbol} Company`,
        price: quote.price || 0,
        change: quote.change || 0,
        changePercent: quote.changesPercentage || 0,
        volume: quote.volume || 0,
        marketCap: quote.marketCap || 0,
        pe: quote.pe || 0,
        high52Week: quote.yearHigh || 0,
        low52Week: quote.yearLow || 0,
        timestamp: new Date().toISOString()
      };
    } catch (error) {
      console.error(`[FMP] Quote fetch failed for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Fetch earnings data
   */
  public async fetchEarningsData(symbol: string): Promise<FMPEarningsData | null> {
    if (!this.apiKey) {
      console.warn('[FMP] API key not configured');
      return null;
    }

    try {
      console.log(`[FMP] Fetching earnings data for ${symbol} with limit 15`);
      
      // Fetch historical earnings data (this endpoint actually returns data)
      const response = await fetch(`${this.baseUrl}/historical/earning_calendar/${symbol}?apikey=${this.apiKey}`);

      if (!response.ok) {
        console.error(`[FMP] Earnings API failed: ${response.status}`);
        return null;
      }

      const earningsData = await response.json();
      console.log(`[FMP] Raw earnings data for ${symbol}:`, earningsData.slice(0, 3));

      if (!Array.isArray(earningsData) || earningsData.length === 0) {
        console.warn(`[FMP] No earnings data found for ${symbol}`);
        return null;
      }

      const today = new Date();
      today.setHours(0, 0, 0, 0); // Set to start of day for accurate comparison

      // Sort by date (most recent first) and take first 15
      const sortedEarnings = earningsData
        .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
        .slice(0, 15);

      // Separate past and future earnings
      const pastEarnings = sortedEarnings
        .filter(e => new Date(e.date) < today && (e.eps !== null || e.epsEstimated !== null))
        .slice(0, 7); // Last 7 releases with data

      // Get future earnings and prioritize those with estimates
      const allFutureEarnings = sortedEarnings
        .filter(e => new Date(e.date) >= today);
      
      // Sort future earnings chronologically (earliest first) for proper ordering
      const chronologicalFuture = allFutureEarnings
        .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
      
      // Get the first future earnings with estimates (priority)
      const firstWithEstimates = chronologicalFuture
        .find(e => e.epsEstimated !== null || e.revenueEstimated !== null);
      
      // Get the chronologically next future earnings after the first
      const remainingFuture = chronologicalFuture
        .filter(e => e.date !== firstWithEstimates?.date);
      
      // Take up to 2 future earnings: first with estimates, then chronologically next
      const futureEarnings = [
        firstWithEstimates,
        ...remainingFuture.slice(0, 1)
      ].filter(Boolean).slice(0, 2);

      console.log(`[FMP] Found ${pastEarnings.length} past earnings, ${futureEarnings.length} future earnings for ${symbol}`);

      // Process historical earnings (past 7 releases with actual data)
      const historicalEarnings = pastEarnings.map((earning: any) => {
        // Historical endpoint uses different field names
        const actualEPS = earning.eps;
        const estimatedEPS = earning.epsEstimated;
        const actualRevenue = earning.revenue;
        const estimatedRevenue = earning.revenueEstimated;
        
        // Calculate surprises only if we have both actual and estimated values
        const epsSurprise = (actualEPS !== null && estimatedEPS !== null) ? actualEPS - estimatedEPS : null;
        const epsSurprisePercent = (epsSurprise !== null && estimatedEPS !== 0) ? (epsSurprise / Math.abs(estimatedEPS)) * 100 : null;
        const revenueSurprise = (actualRevenue !== null && estimatedRevenue !== null) ? actualRevenue - estimatedRevenue : null;
        const revenueSurprisePercent = (revenueSurprise !== null && estimatedRevenue !== 0) ? (revenueSurprise / estimatedRevenue) * 100 : null;
        
        return {
          date: earning.date,
          actualEPS,
          estimatedEPS,
          actualRevenue,
          estimatedRevenue,
          epsSurprise,
          epsSurprisePercent: epsSurprisePercent ? Math.round(epsSurprisePercent * 100) / 100 : null,
          revenueSurprise,
          revenueSurprisePercent: revenueSurprisePercent ? Math.round(revenueSurprisePercent * 100) / 100 : null,
          priceMoveBefore: 0, // Would need historical price data
          priceMoveAfter: 0,  // Would need historical price data
          priceMovePercent: 0 // Would need historical price data
        };
      });

      // Process future earnings (2 upcoming releases)
      const upcomingEarnings = futureEarnings.map((earning: any) => ({
        date: earning.date,
        actualEPS: null, // Future earnings don't have actuals yet
        estimatedEPS: earning.epsEstimated,
        actualRevenue: null, // Future earnings don't have actuals yet
        estimatedRevenue: earning.revenueEstimated,
        epsSurprise: null,
        epsSurprisePercent: null,
        revenueSurprise: null,
        revenueSurprisePercent: null,
        priceMoveBefore: 0,
        priceMoveAfter: 0,
        priceMovePercent: 0
      }));

      // Calculate comprehensive stats
      const avgMove = historicalEarnings.length > 0 
        ? historicalEarnings.reduce((sum: number, e: any) => sum + Math.abs(e.priceMovePercent), 0) / historicalEarnings.length
        : 5;
      
      const epsBeatCount = historicalEarnings.filter((e: any) => e.epsSurprise > 0).length;
      const beatRate = historicalEarnings.length > 0 ? epsBeatCount / historicalEarnings.length : 0.6;
      const avgBeat = historicalEarnings.length > 0 
        ? historicalEarnings.reduce((sum: number, e: any) => sum + e.epsSurprise, 0) / historicalEarnings.length
        : 0;
      
      const revenueBeatCount = historicalEarnings.filter((e: any) => e.revenueSurprise > 0).length;
      const revenueBeatRate = historicalEarnings.length > 0 ? revenueBeatCount / historicalEarnings.length : 0.65;
      const avgRevenueBeat = historicalEarnings.length > 0 
        ? historicalEarnings.reduce((sum: number, e: any) => sum + e.revenueSurprisePercent, 0) / historicalEarnings.length
        : 0;

      return {
        symbol,
        nextEarningsDate: upcomingEarnings.length > 0 ? upcomingEarnings[0].date : null,
        nextEarningsTime: 'UNKNOWN', // FMP calendar doesn't provide timing
        estimatedEPS: upcomingEarnings.length > 0 ? upcomingEarnings[0].estimatedEPS : null,
        estimatedRevenue: upcomingEarnings.length > 0 ? upcomingEarnings[0].estimatedRevenue : null,
        historicalEarnings,
        upcomingEarnings, // Include upcoming earnings data
        stats: {
          avgMove,
          avgAbsMove: avgMove,
          beatRate,
          avgBeat,
          revenueBeatRate,
          avgRevenueBeat
        },
        dataSource: 'fmp'
      };
    } catch (error) {
      console.error(`[FMP] Earnings fetch failed for ${symbol}:`, error);
      return null;
    }
  }
}

// Export singleton instance
export const fmpService = FMPService.getInstance();

// Export helper functions
export async function fetchFMPQuote(symbol: string): Promise<FMPQuoteData | null> {
  return fmpService.fetchQuote(symbol);
}

export async function fetchFMPEarnings(symbol: string): Promise<FMPEarningsData | null> {
  return fmpService.fetchEarningsData(symbol);
}
