/**
 * Polygon.io Options Market Data Service
 * Provides comprehensive options chain data with real-time pricing, Greeks, and analytics
 */

export interface PolygonOptionContract {
  break_even_price: number;
  day: {
    change: number;
    change_percent: number;
    close: number;
    high: number;
    last_updated: number;
    low: number;
    open: number;
    previous_close: number;
    volume: number;
    vwap: number;
  };
  details: {
    contract_type: 'call' | 'put';
    exercise_style: 'american' | 'european';
    expiration_date: string;
    shares_per_contract: number;
    strike_price: number;
    ticker: string;
  };
  fmv?: number; // Fair Market Value (Business plan only)
  greeks?: {
    delta: number;
    gamma: number;
    theta: number;
    vega: number;
  };
  implied_volatility?: number;
  last_quote?: {
    ask: number;
    ask_size: number;
    bid: number;
    bid_size: number;
    exchange: number;
    last_updated: number;
  };
  last_trade?: {
    conditions: number[];
    exchange: number;
    price: number;
    sip_timestamp: number;
    size: number;
    timeframe: string;
  };
  open_interest: number;
  underlying_asset: {
    change_to_break_even: number;
    last_updated: number;
    market_status: string;
    price: number;
    ticker: string;
    timeframe: string;
    value: number;
  };
}

export interface PolygonOptionsChainResponse {
  next_url?: string;
  request_id: string;
  results?: PolygonOptionContract[];
  status: string;
}

export interface PolygonOptionsContract {
  additional_underlyings?: any[];
  cfi?: string;
  contract_type: 'call' | 'put';
  correction?: number;
  exercise_style: 'american' | 'european';
  expiration_date: string;
  primary_exchange?: string;
  shares_per_contract: number;
  strike_price: number;
  ticker: string;
  underlying_ticker: string;
}

export interface PolygonAllContractsResponse {
  next_url?: string;
  request_id: string;
  results?: PolygonOptionsContract[];
  status: string;
  count?: number;
}

export interface ContractDiscoveryResult {
  symbol: string;
  totalContracts: number;
  expirations: Array<{
    date: string;
    daysToExpiry: number;
    contractCount: number;
    callCount: number;
    putCount: number;
    strikeRange: {
      min: number;
      max: number;
      count: number;
    };
  }>;
  strikeAnalysis: {
    minStrike: number;
    maxStrike: number;
    totalStrikes: number;
    averageSpacing: number;
  };
  contractTypes: {
    calls: number;
    puts: number;
  };
  exerciseStyles: {
    american: number;
    european: number;
  };
  dataSource: 'polygon';
  lastUpdated: string;
}

export interface PolygonContractSnapshot {
  break_even_price: number;
  day: {
    change: number;
    change_percent: number;
    close: number;
    high: number;
    last_updated: number;
    low: number;
    open: number;
    previous_close: number;
    volume: number;
    vwap: number;
  };
  details: {
    contract_type: 'call' | 'put';
    exercise_style: 'american' | 'european';
    expiration_date: string;
    shares_per_contract: number;
    strike_price: number;
    ticker: string;
  };
  fmv?: number;
  greeks?: {
    delta: number;
    gamma: number;
    theta: number;
    vega: number;
  };
  implied_volatility?: number;
  last_quote?: {
    ask: number;
    ask_exchange?: number;
    ask_size: number;
    bid: number;
    bid_exchange?: number;
    bid_size: number;
    last_updated: number;
    midpoint?: number;
    timeframe?: string;
  };
  last_trade?: {
    conditions?: number[];
    exchange?: number;
    price: number;
    sip_timestamp: number;
    size: number;
    timeframe?: string;
  };
  open_interest: number;
  underlying_asset: {
    change_to_break_even: number;
    last_updated: number;
    price: number;
    ticker: string;
    timeframe?: string;
  };
}

export interface PolygonContractSnapshotResponse {
  request_id: string;
  results?: PolygonContractSnapshot;
  status: string;
}

export interface ContractAnalysis {
  contract: {
    ticker: string;
    underlyingSymbol: string;
    contractType: 'call' | 'put';
    strikePrice: number;
    expirationDate: string;
    daysToExpiry: number;
    exerciseStyle: 'american' | 'european';
    sharesPerContract: number;
  };
  pricing: {
    last: number;
    bid: number;
    ask: number;
    midpoint: number;
    spread: number;
    spreadPercent: number;
    breakEvenPrice: number;
    fmv?: number;
  };
  dayStats: {
    open: number;
    high: number;
    low: number;
    close: number;
    change: number;
    changePercent: number;
    volume: number;
    vwap: number;
    previousClose: number;
  };
  greeks?: {
    delta: number;
    gamma: number;
    theta: number;
    vega: number;
  };
  volatility: {
    impliedVolatility?: number;
    ivRank?: number; // Could be calculated if we have historical IV data
  };
  market: {
    openInterest: number;
    bidSize: number;
    askSize: number;
    lastTradePrice: number;
    lastTradeSize: number;
    lastTradeTime: string;
  };
  underlying: {
    symbol: string;
    price: number;
    changeToBreakEven: number;
    intrinsicValue: number;
    timeValue: number;
    moneyness: 'ITM' | 'ATM' | 'OTM';
    distanceFromStrike: number;
    distanceFromStrikePercent: number;
  };
  analysis: {
    liquidityScore: number; // Based on volume, OI, spread
    riskLevel: 'Low' | 'Medium' | 'High';
    timeDecayRisk: 'Low' | 'Medium' | 'High';
    profitPotential: 'Low' | 'Medium' | 'High';
  };
  dataSource: 'polygon';
  lastUpdated: string;
}

export interface EnhancedOptionsChain {
  symbol: string;
  underlyingPrice: number;
  expirations: Array<{
    date: string;
    daysToExpiry: number;
    strikes: Array<{
      strike: number;
      call?: {
        ticker: string;
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
        breakEven: number;
        fmv?: number;
        intrinsicValue: number;
        timeValue: number;
      };
      put?: {
        ticker: string;
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
        breakEven: number;
        fmv?: number;
        intrinsicValue: number;
        timeValue: number;
      };
    }>;
  }>;
  dataSource: 'polygon';
  lastUpdated: string;
}

class PolygonOptionsService {
  private static instance: PolygonOptionsService;
  private apiKey: string;
  private baseUrl = 'https://api.polygon.io';

  constructor() {
    this.apiKey = process.env.POLYGON_API_KEY || '';
    if (!this.apiKey) {
      console.warn('[Polygon Options] API key not configured');
    }
  }

  static getInstance(): PolygonOptionsService {
    if (!PolygonOptionsService.instance) {
      PolygonOptionsService.instance = new PolygonOptionsService();
    }
    return PolygonOptionsService.instance;
  }

  /**
   * Fetch comprehensive options chain for a symbol using Polygon Option Chain Snapshot
   */
  async getOptionsChain(symbol: string, expirationDate?: string): Promise<EnhancedOptionsChain | null> {
    if (!this.apiKey) {
      console.warn('[Polygon Options] API key not configured');
      return null;
    }

    try {
      console.log(`[Polygon Options] Fetching options chain for ${symbol}`);

      // Build query parameters
      const params = new URLSearchParams({
        'order': 'asc',
        'limit': '250', // Maximum allowed
        'sort': 'ticker'
      });

      if (expirationDate) {
        params.append('expiration_date', expirationDate);
      }

      const response = await fetch(
        `${this.baseUrl}/v3/snapshot/options/${symbol}?${params}&apikey=${this.apiKey}`
      );

      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('POLYGON_RATE_LIMITED');
        }
        throw new Error(`Polygon Options API error: ${response.status}`);
      }

      const data: PolygonOptionsChainResponse = await response.json();

      if (!data.results || data.results.length === 0) {
        console.log(`[Polygon Options] No options data found for ${symbol}`);
        return null;
      }

      console.log(`[Polygon Options] Retrieved ${data.results.length} contracts for ${symbol}`);

      // Transform Polygon data to our enhanced format
      return this.transformToEnhancedChain(symbol, data.results);

    } catch (error) {
      console.error(`[Polygon Options] Chain fetch failed for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Transform Polygon option contracts to our enhanced chain format
   */
  private transformToEnhancedChain(symbol: string, contracts: PolygonOptionContract[]): EnhancedOptionsChain {
    // Get underlying price from first contract
    const underlyingPrice = contracts[0]?.underlying_asset?.price || 0;

    // Group contracts by expiration date
    const expirationGroups = new Map<string, PolygonOptionContract[]>();
    
    contracts.forEach(contract => {
      const expDate = contract.details.expiration_date;
      if (!expirationGroups.has(expDate)) {
        expirationGroups.set(expDate, []);
      }
      expirationGroups.get(expDate)!.push(contract);
    });

    // Build expirations array
    const expirations = Array.from(expirationGroups.entries()).map(([date, expContracts]) => {
      const daysToExpiry = Math.ceil((new Date(date).getTime() - Date.now()) / (1000 * 60 * 60 * 24));

      // Group by strike price
      const strikeGroups = new Map<number, { calls: PolygonOptionContract[], puts: PolygonOptionContract[] }>();
      
      expContracts.forEach(contract => {
        const strike = contract.details.strike_price;
        if (!strikeGroups.has(strike)) {
          strikeGroups.set(strike, { calls: [], puts: [] });
        }
        
        if (contract.details.contract_type === 'call') {
          strikeGroups.get(strike)!.calls.push(contract);
        } else {
          strikeGroups.get(strike)!.puts.push(contract);
        }
      });

      // Build strikes array
      const strikes = Array.from(strikeGroups.entries())
        .sort(([a], [b]) => a - b)
        .map(([strike, { calls, puts }]) => {
          const callContract = calls[0];
          const putContract = puts[0];

          const strikeData: any = { strike };

          // Process call data
          if (callContract) {
            const intrinsicValue = Math.max(0, underlyingPrice - strike);
            const lastPrice = callContract.day?.close || callContract.last_trade?.price || 0;
            const timeValue = Math.max(0, lastPrice - intrinsicValue);

            strikeData.call = {
              ticker: callContract.details.ticker,
              bid: callContract.last_quote?.bid || 0,
              ask: callContract.last_quote?.ask || 0,
              last: lastPrice,
              volume: callContract.day?.volume || 0,
              openInterest: callContract.open_interest || 0,
              impliedVolatility: callContract.implied_volatility || 0,
              delta: callContract.greeks?.delta,
              gamma: callContract.greeks?.gamma,
              theta: callContract.greeks?.theta,
              vega: callContract.greeks?.vega,
              breakEven: callContract.break_even_price,
              fmv: callContract.fmv,
              intrinsicValue,
              timeValue
            };
          }

          // Process put data
          if (putContract) {
            const intrinsicValue = Math.max(0, strike - underlyingPrice);
            const lastPrice = putContract.day?.close || putContract.last_trade?.price || 0;
            const timeValue = Math.max(0, lastPrice - intrinsicValue);

            strikeData.put = {
              ticker: putContract.details.ticker,
              bid: putContract.last_quote?.bid || 0,
              ask: putContract.last_quote?.ask || 0,
              last: lastPrice,
              volume: putContract.day?.volume || 0,
              openInterest: putContract.open_interest || 0,
              impliedVolatility: putContract.implied_volatility || 0,
              delta: putContract.greeks?.delta,
              gamma: putContract.greeks?.gamma,
              theta: putContract.greeks?.theta,
              vega: putContract.greeks?.vega,
              breakEven: putContract.break_even_price,
              fmv: putContract.fmv,
              intrinsicValue,
              timeValue
            };
          }

          return strikeData;
        });

      return {
        date,
        daysToExpiry,
        strikes
      };
    }).sort((a, b) => a.daysToExpiry - b.daysToExpiry);

    return {
      symbol,
      underlyingPrice,
      expirations,
      dataSource: 'polygon',
      lastUpdated: new Date().toISOString()
    };
  }

  /**
   * Get options chain for specific expiration date
   */
  async getOptionsChainByExpiration(symbol: string, expirationDate: string): Promise<EnhancedOptionsChain | null> {
    return this.getOptionsChain(symbol, expirationDate);
  }

  /**
   * Get ATM options for expected move calculations
   */
  async getATMOptions(symbol: string, expirationDate?: string): Promise<{
    call?: any;
    put?: any;
    underlyingPrice: number;
  } | null> {
    const chain = await this.getOptionsChain(symbol, expirationDate);
    if (!chain || chain.expirations.length === 0) {
      return null;
    }

    // Use first expiration if none specified
    const expiration = chain.expirations[0];
    const underlyingPrice = chain.underlyingPrice;

    // Find ATM strike (closest to underlying price)
    let atmStrike = expiration.strikes[0];
    let minDiff = Math.abs(atmStrike.strike - underlyingPrice);

    expiration.strikes.forEach(strike => {
      const diff = Math.abs(strike.strike - underlyingPrice);
      if (diff < minDiff) {
        minDiff = diff;
        atmStrike = strike;
      }
    });

    return {
      call: atmStrike.call,
      put: atmStrike.put,
      underlyingPrice
    };
  }

  /**
   * Get all available options contracts for a symbol using Polygon All Contracts endpoint
   */
  async getAllContracts(
    symbol: string, 
    limit: number = 250,
    contractType?: 'call' | 'put',
    expirationDate?: string
  ): Promise<ContractDiscoveryResult | null> {
    if (!this.apiKey) {
      console.warn('[Polygon Options] API key not configured');
      return null;
    }

    try {
      console.log(`[Polygon Options] Fetching all contracts for ${symbol}`);

      // Build query parameters
      const params = new URLSearchParams({
        'underlying_ticker': symbol,
        'order': 'asc',
        'limit': limit.toString(),
        'sort': 'expiration_date'
      });

      if (contractType) {
        params.append('contract_type', contractType);
      }

      if (expirationDate) {
        params.append('expiration_date', expirationDate);
      }

      const response = await fetch(
        `${this.baseUrl}/v3/reference/options/contracts?${params}&apikey=${this.apiKey}`
      );

      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('POLYGON_RATE_LIMITED');
        }
        throw new Error(`Polygon All Contracts API error: ${response.status}`);
      }

      const data: PolygonAllContractsResponse = await response.json();

      if (!data.results || data.results.length === 0) {
        console.log(`[Polygon Options] No contracts found for ${symbol}`);
        return null;
      }

      console.log(`[Polygon Options] Retrieved ${data.results.length} contracts for ${symbol}`);

      // Transform to contract discovery result
      return this.transformToContractDiscovery(symbol, data.results);

    } catch (error) {
      console.error(`[Polygon Options] All contracts fetch failed for ${symbol}:`, error);
      return null;
    }
  }

  /**
   * Transform Polygon contracts to contract discovery format
   */
  private transformToContractDiscovery(symbol: string, contracts: PolygonOptionsContract[]): ContractDiscoveryResult {
    // Group contracts by expiration date
    const expirationGroups = new Map<string, PolygonOptionsContract[]>();
    
    contracts.forEach(contract => {
      const expDate = contract.expiration_date;
      if (!expirationGroups.has(expDate)) {
        expirationGroups.set(expDate, []);
      }
      expirationGroups.get(expDate)!.push(contract);
    });

    // Analyze expirations
    const expirations = Array.from(expirationGroups.entries()).map(([date, expContracts]) => {
      const daysToExpiry = Math.ceil((new Date(date).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
      
      const callCount = expContracts.filter(c => c.contract_type === 'call').length;
      const putCount = expContracts.filter(c => c.contract_type === 'put').length;
      
      const strikes = expContracts.map(c => c.strike_price);
      const minStrike = Math.min(...strikes);
      const maxStrike = Math.max(...strikes);
      const uniqueStrikes = [...new Set(strikes)];

      return {
        date,
        daysToExpiry,
        contractCount: expContracts.length,
        callCount,
        putCount,
        strikeRange: {
          min: minStrike,
          max: maxStrike,
          count: uniqueStrikes.length
        }
      };
    }).sort((a, b) => a.daysToExpiry - b.daysToExpiry);

    // Overall strike analysis
    const allStrikes = contracts.map(c => c.strike_price);
    const uniqueStrikes = [...new Set(allStrikes)].sort((a, b) => a - b);
    const minStrike = Math.min(...uniqueStrikes);
    const maxStrike = Math.max(...uniqueStrikes);
    
    // Calculate average strike spacing
    let totalSpacing = 0;
    for (let i = 1; i < uniqueStrikes.length; i++) {
      totalSpacing += uniqueStrikes[i] - uniqueStrikes[i - 1];
    }
    const averageSpacing = uniqueStrikes.length > 1 ? totalSpacing / (uniqueStrikes.length - 1) : 0;

    // Contract type analysis
    const callCount = contracts.filter(c => c.contract_type === 'call').length;
    const putCount = contracts.filter(c => c.contract_type === 'put').length;

    // Exercise style analysis
    const americanCount = contracts.filter(c => c.exercise_style === 'american').length;
    const europeanCount = contracts.filter(c => c.exercise_style === 'european').length;

    return {
      symbol,
      totalContracts: contracts.length,
      expirations,
      strikeAnalysis: {
        minStrike,
        maxStrike,
        totalStrikes: uniqueStrikes.length,
        averageSpacing
      },
      contractTypes: {
        calls: callCount,
        puts: putCount
      },
      exerciseStyles: {
        american: americanCount,
        european: europeanCount
      },
      dataSource: 'polygon',
      lastUpdated: new Date().toISOString()
    };
  }

  /**
   * Get contracts for specific expiration date
   */
  async getContractsByExpiration(symbol: string, expirationDate: string): Promise<ContractDiscoveryResult | null> {
    return this.getAllContracts(symbol, 250, undefined, expirationDate);
  }

  /**
   * Get only call or put contracts
   */
  async getContractsByType(symbol: string, contractType: 'call' | 'put'): Promise<ContractDiscoveryResult | null> {
    return this.getAllContracts(symbol, 250, contractType);
  }

  /**
   * Get detailed snapshot of a specific options contract using Polygon Contract Snapshot endpoint
   */
  async getContractSnapshot(underlyingSymbol: string, optionContract: string): Promise<ContractAnalysis | null> {
    if (!this.apiKey) {
      console.warn('[Polygon Options] API key not configured');
      return null;
    }

    try {
      console.log(`[Polygon Options] Fetching contract snapshot for ${optionContract}`);

      const response = await fetch(
        `${this.baseUrl}/v3/snapshot/options/${underlyingSymbol}/${optionContract}?apikey=${this.apiKey}`
      );

      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('POLYGON_RATE_LIMITED');
        }
        throw new Error(`Polygon Contract Snapshot API error: ${response.status}`);
      }

      const data: PolygonContractSnapshotResponse = await response.json();

      if (!data.results) {
        console.log(`[Polygon Options] No contract snapshot found for ${optionContract}`);
        return null;
      }

      console.log(`[Polygon Options] Contract snapshot retrieved for ${optionContract}`);

      // Transform to contract analysis format
      return this.transformToContractAnalysis(data.results);

    } catch (error) {
      console.error(`[Polygon Options] Contract snapshot fetch failed for ${optionContract}:`, error);
      return null;
    }
  }

  /**
   * Transform Polygon contract snapshot to contract analysis format
   */
  private transformToContractAnalysis(snapshot: PolygonContractSnapshot): ContractAnalysis {
    const underlyingPrice = snapshot.underlying_asset.price;
    const strikePrice = snapshot.details.strike_price;
    const contractType = snapshot.details.contract_type;
    
    // Calculate intrinsic and time value
    const intrinsicValue = contractType === 'call' 
      ? Math.max(0, underlyingPrice - strikePrice)
      : Math.max(0, strikePrice - underlyingPrice);
    
    const lastPrice = snapshot.day.close || snapshot.last_trade?.price || 0;
    const timeValue = Math.max(0, lastPrice - intrinsicValue);
    
    // Calculate moneyness
    let moneyness: 'ITM' | 'ATM' | 'OTM';
    const distanceFromStrike = Math.abs(underlyingPrice - strikePrice);
    const distanceFromStrikePercent = (distanceFromStrike / underlyingPrice) * 100;
    
    if (distanceFromStrikePercent < 2) {
      moneyness = 'ATM';
    } else if (contractType === 'call') {
      moneyness = underlyingPrice > strikePrice ? 'ITM' : 'OTM';
    } else {
      moneyness = underlyingPrice < strikePrice ? 'ITM' : 'OTM';
    }
    
    // Calculate bid-ask spread
    const bid = snapshot.last_quote?.bid || 0;
    const ask = snapshot.last_quote?.ask || 0;
    const midpoint = snapshot.last_quote?.midpoint || (bid + ask) / 2;
    const spread = ask - bid;
    const spreadPercent = midpoint > 0 ? (spread / midpoint) * 100 : 0;
    
    // Calculate days to expiry
    const expirationDate = new Date(snapshot.details.expiration_date);
    const daysToExpiry = Math.ceil((expirationDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
    
    // Calculate liquidity score (0-100)
    const volume = snapshot.day.volume || 0;
    const openInterest = snapshot.open_interest || 0;
    const liquidityScore = Math.min(100, 
      (volume * 0.3) + 
      (Math.min(openInterest / 100, 50) * 0.5) + 
      (Math.max(0, 50 - spreadPercent * 10) * 0.2)
    );
    
    // Risk assessments
    const riskLevel: 'Low' | 'Medium' | 'High' = 
      daysToExpiry > 30 ? 'Low' : 
      daysToExpiry > 7 ? 'Medium' : 'High';
    
    const timeDecayRisk: 'Low' | 'Medium' | 'High' = 
      daysToExpiry > 45 ? 'Low' : 
      daysToExpiry > 14 ? 'Medium' : 'High';
    
    const profitPotential: 'Low' | 'Medium' | 'High' = 
      moneyness === 'OTM' && daysToExpiry < 7 ? 'Low' :
      moneyness === 'ATM' ? 'High' : 'Medium';

    return {
      contract: {
        ticker: snapshot.details.ticker,
        underlyingSymbol: snapshot.underlying_asset.ticker,
        contractType: snapshot.details.contract_type,
        strikePrice: snapshot.details.strike_price,
        expirationDate: snapshot.details.expiration_date,
        daysToExpiry,
        exerciseStyle: snapshot.details.exercise_style,
        sharesPerContract: snapshot.details.shares_per_contract
      },
      pricing: {
        last: lastPrice,
        bid,
        ask,
        midpoint,
        spread,
        spreadPercent,
        breakEvenPrice: snapshot.break_even_price,
        fmv: snapshot.fmv
      },
      dayStats: {
        open: snapshot.day.open,
        high: snapshot.day.high,
        low: snapshot.day.low,
        close: snapshot.day.close,
        change: snapshot.day.change,
        changePercent: snapshot.day.change_percent,
        volume: snapshot.day.volume,
        vwap: snapshot.day.vwap,
        previousClose: snapshot.day.previous_close
      },
      greeks: snapshot.greeks,
      volatility: {
        impliedVolatility: snapshot.implied_volatility
      },
      market: {
        openInterest: snapshot.open_interest,
        bidSize: snapshot.last_quote?.bid_size || 0,
        askSize: snapshot.last_quote?.ask_size || 0,
        lastTradePrice: snapshot.last_trade?.price || 0,
        lastTradeSize: snapshot.last_trade?.size || 0,
        lastTradeTime: snapshot.last_trade?.sip_timestamp 
          ? new Date(snapshot.last_trade.sip_timestamp / 1000000).toISOString()
          : ''
      },
      underlying: {
        symbol: snapshot.underlying_asset.ticker,
        price: underlyingPrice,
        changeToBreakEven: snapshot.underlying_asset.change_to_break_even,
        intrinsicValue,
        timeValue,
        moneyness,
        distanceFromStrike,
        distanceFromStrikePercent
      },
      analysis: {
        liquidityScore,
        riskLevel,
        timeDecayRisk,
        profitPotential
      },
      dataSource: 'polygon',
      lastUpdated: new Date().toISOString()
    };
  }
}

export default PolygonOptionsService;
