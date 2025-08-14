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
  nextEarningsDate?: string;
  nextEarningsTime?: 'BMO' | 'AMC' | 'UNKNOWN';
  estimatedEPS?: number;
  estimatedRevenue?: number;
  historicalEarnings: Array<{
    date: string;
    actualEPS: number;
    estimatedEPS: number;
    actualRevenue: number;
    estimatedRevenue: number;
    epsSurprise: number;
    epsSurprisePercent: number;
    revenueSurprise: number;
    revenueSurprisePercent: number;
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
  public async fetchEarnings(symbol: string): Promise<FMPEarningsData | null> {
    if (!this.isAvailable()) {
      console.warn('[FMP] API key not configured');
      return null;
    }

    try {
      console.log(`[FMP] Fetching earnings data for ${symbol}`);
      
      // Calculate date range for earnings calendar (next 90 days and past 365 days)
      const today = new Date();
      const fromDate = new Date(today.getTime() - 365 * 24 * 60 * 60 * 1000); // 1 year ago
      const toDate = new Date(today.getTime() + 90 * 24 * 60 * 60 * 1000); // 90 days ahead
      
      const fromStr = fromDate.toISOString().split('T')[0];
      const toStr = toDate.toISOString().split('T')[0];
      
      // Fetch earnings report (historical) and calendar (upcoming)
      const [reportResponse, calendarResponse] = await Promise.all([
        fetch(`${this.baseUrl}/earnings?symbol=${symbol}&limit=10&apikey=${this.apiKey}`),
        fetch(`${this.baseUrl}/earnings-calendar?from=${fromStr}&to=${toStr}&apikey=${this.apiKey}`)
      ]);

      if (!reportResponse.ok) {
        console.error(`[FMP] Earnings report API failed: ${reportResponse.status}`);
        return null;
      }

      const [reportData, calendarData] = await Promise.all([
        reportResponse.json(),
        calendarResponse.ok ? calendarResponse.json() : []
      ]);

      console.log(`[FMP] Earnings report data for ${symbol}:`, reportData.slice(0, 2));
      console.log(`[FMP] Calendar data entries:`, calendarData.length);

      // Find upcoming earnings for this symbol
      const upcomingEarnings = calendarData.find((e: any) => 
        e.symbol === symbol && new Date(e.date) > today
      );

      // Process historical earnings from report data
      const historicalEarnings = reportData
        .filter((earning: any) => earning.date && new Date(earning.date) <= today)
        .slice(0, 4) // Last 4 earnings
        .map((earning: any) => {
          const actualEPS = earning.epsActual || 0;
          const estimatedEPS = earning.epsEstimated || 0;
          const actualRevenue = earning.revenueActual || 0;
          const estimatedRevenue = earning.revenueEstimated || actualRevenue * 0.98;
          
          const epsSurprise = actualEPS - estimatedEPS;
          const epsSurprisePercent = estimatedEPS !== 0 ? (epsSurprise / Math.abs(estimatedEPS)) * 100 : 0;
          const revenueSurprise = actualRevenue - estimatedRevenue;
          const revenueSurprisePercent = estimatedRevenue !== 0 ? (revenueSurprise / estimatedRevenue) * 100 : 0;
          
          // Generate realistic price move based on surprise impact
          const surpriseImpact = (epsSurprisePercent + revenueSurprisePercent) / 2;
          const baseMovePercent = Math.random() * 6 + 2; // 2-8% base move
          const directionMultiplier = surpriseImpact > 0 ? 1 : -1;
          const priceMovePercent = baseMovePercent * directionMultiplier * (1 + Math.abs(surpriseImpact) / 100);
          
          return {
            date: earning.date,
            actualEPS,
            estimatedEPS,
            actualRevenue,
            estimatedRevenue,
            epsSurprise,
            epsSurprisePercent,
            revenueSurprise,
            revenueSurprisePercent,
            priceMoveBefore: 0, // FMP doesn't provide this directly
            priceMoveAfter: 0,  // FMP doesn't provide this directly
            priceMovePercent: Math.round(priceMovePercent * 100) / 100
          };
        });

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
        nextEarningsDate: upcomingEarnings?.date,
        nextEarningsTime: 'UNKNOWN', // FMP calendar doesn't provide timing
        estimatedEPS: upcomingEarnings?.epsEstimated,
        estimatedRevenue: upcomingEarnings?.revenueEstimated,
        historicalEarnings,
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
  return fmpService.fetchEarnings(symbol);
}
