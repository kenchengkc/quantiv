/**
 * Expected Move Service
 * Calculates expected moves using both straddle and IV methods
 * Provides price bands for 1σ and 2σ moves
 */

import { blackScholes, findATMStrike, type BSParams } from '../pricing/blackScholes';

export interface ChainData {
  spot: number;
  strikes: number[];
  calls: Array<{
    strike: number;
    mid: number;
    bid: number;
    ask: number;
    iv?: number;
    volume?: number;
    openInterest?: number;
  }>;
  puts: Array<{
    strike: number;
    mid: number;
    bid: number;
    ask: number;
    iv?: number;
    volume?: number;
    openInterest?: number;
  }>;
  expiryDate: string;
  daysToExpiry: number;
}

export interface ATMData {
  strike: number;
  callMid: number;
  putMid: number;
  iv: number;
  T: number; // Time to expiry in years
}

export interface ExpectedMoveResult {
  straddle: {
    abs: number;    // Absolute dollar amount
    pct: number;    // Percentage of stock price
  };
  iv: {
    abs: number;    // IV-based absolute move
    pct: number;    // IV-based percentage move
  };
  bands: {
    oneSigma: {
      upper: number;
      lower: number;
    };
    twoSigma: {
      upper: number;
      lower: number;
    };
  };
  confidence: {
    straddle: 'high' | 'medium' | 'low';
    iv: 'high' | 'medium' | 'low';
  };
}

/**
 * Find ATM options data from chain
 */
export function findATMData(chain: ChainData): ATMData {
  const atmStrike = findATMStrike(chain.strikes, chain.spot);
  
  // Find corresponding call and put
  const atmCall = chain.calls.find(c => c.strike === atmStrike);
  const atmPut = chain.puts.find(p => p.strike === atmStrike);
  
  if (!atmCall || !atmPut) {
    throw new Error(`ATM options not found for strike ${atmStrike}`);
  }

  // Calculate implied volatility - prefer average if both available
  let iv: number;
  const callIV = atmCall.iv;
  const putIV = atmPut.iv;
  
  if (callIV && putIV) {
    iv = (callIV + putIV) / 2;
  } else if (callIV) {
    iv = callIV;
  } else if (putIV) {
    iv = putIV;
  } else {
    // Fallback: estimate IV from option prices
    iv = estimateIVFromPrices(chain.spot, atmStrike, atmCall.mid, chain.daysToExpiry);
  }

  return {
    strike: atmStrike,
    callMid: atmCall.mid,
    putMid: atmPut.mid,
    iv: iv,
    T: chain.daysToExpiry / 365
  };
}

/**
 * Estimate IV from option prices when not available
 */
function estimateIVFromPrices(spot: number, strike: number, optionPrice: number, daysToExpiry: number): number {
  const T = daysToExpiry / 365;
  const moneyness = spot / strike;
  
  // Simple heuristic based on time value and moneyness
  // This is a rough approximation - real IV should come from data provider
  let baseIV = 0.20; // 20% base volatility
  
  // Adjust for time to expiry
  if (T < 0.1) baseIV *= 1.5; // Short-term options tend to have higher IV
  if (T > 0.5) baseIV *= 0.8; // Longer-term options tend to have lower IV
  
  // Adjust for moneyness
  const timeValue = optionPrice - Math.max(0, spot - strike);
  if (timeValue > 0) {
    baseIV *= Math.min(2.0, timeValue / (spot * 0.02)); // Scale by time value
  }
  
  return Math.max(0.05, Math.min(2.0, baseIV)); // Clamp between 5% and 200%
}

/**
 * Calculate expected move using straddle method
 */
export function calculateStraddleMove(atm: ATMData, spot: number): { abs: number; pct: number } {
  const straddlePrice = atm.callMid + atm.putMid;
  
  return {
    abs: straddlePrice,
    pct: (straddlePrice / spot) * 100
  };
}

/**
 * Calculate expected move using IV method
 */
export function calculateIVMove(atm: ATMData, spot: number): { abs: number; pct: number } {
  const ivMove = spot * atm.iv * Math.sqrt(atm.T);
  
  return {
    abs: ivMove,
    pct: (ivMove / spot) * 100
  };
}

/**
 * Calculate price bands for 1σ and 2σ moves
 */
export function calculatePriceBands(spot: number, ivMove: number): ExpectedMoveResult['bands'] {
  return {
    oneSigma: {
      upper: spot + ivMove,
      lower: spot - ivMove
    },
    twoSigma: {
      upper: spot + (2 * ivMove),
      lower: spot - (2 * ivMove)
    }
  };
}

/**
 * Assess confidence in expected move calculations
 */
export function assessConfidence(chain: ChainData, atm: ATMData): ExpectedMoveResult['confidence'] {
  // Factors that affect confidence:
  // 1. Volume and open interest
  // 2. Bid-ask spreads
  // 3. Time to expiry
  // 4. IV availability and consistency
  
  const atmCall = chain.calls.find(c => c.strike === atm.strike);
  const atmPut = chain.puts.find(p => p.strike === atm.strike);
  
  let straddleConfidence: 'high' | 'medium' | 'low' = 'medium';
  let ivConfidence: 'high' | 'medium' | 'low' = 'medium';
  
  if (atmCall && atmPut) {
    // Check bid-ask spreads
    const callSpread = (atmCall.ask - atmCall.bid) / atmCall.mid;
    const putSpread = (atmPut.ask - atmPut.bid) / atmPut.mid;
    const avgSpread = (callSpread + putSpread) / 2;
    
    if (avgSpread < 0.10) { // Tight spreads (10%)
      straddleConfidence = 'high';
    } else if (avgSpread > 0.25) { // Wide spreads (25%)
      straddleConfidence = 'low';
    }
    
    // Check volume (if available) - adjust confidence but don't override good spreads
    const totalVolume = (atmCall.volume || 0) + (atmPut.volume || 0);
    if (totalVolume > 100) {
      // High volume reinforces confidence
      if (straddleConfidence === 'medium') straddleConfidence = 'high';
    } else if (totalVolume < 10) {
      // Low volume reduces confidence
      if (straddleConfidence === 'high') straddleConfidence = 'medium';
      if (straddleConfidence === 'medium') straddleConfidence = 'low';
    }
  }
  
  // IV confidence based on availability and time to expiry
  if (atmCall?.iv && atmPut?.iv) {
    const ivDiff = Math.abs(atmCall.iv - atmPut.iv);
    if (ivDiff < 0.02) { // IVs are consistent
      ivConfidence = 'high';
    } else if (ivDiff > 0.05) { // IVs are inconsistent
      ivConfidence = 'low';
    }
  } else if (!atmCall?.iv && !atmPut?.iv) {
    ivConfidence = 'low'; // No IV data available
  }
  
  // Adjust for time to expiry
  if (atm.T < 0.02) { // Less than a week
    straddleConfidence = straddleConfidence === 'high' ? 'medium' : 'low';
    ivConfidence = ivConfidence === 'high' ? 'medium' : 'low';
  }
  
  return {
    straddle: straddleConfidence,
    iv: ivConfidence
  };
}

/**
 * Main function to compute expected move
 */
export function computeExpectedMove(chain: ChainData): ExpectedMoveResult {
  const atm = findATMData(chain);
  const straddleMove = calculateStraddleMove(atm, chain.spot);
  const ivMove = calculateIVMove(atm, chain.spot);
  const bands = calculatePriceBands(chain.spot, ivMove.abs);
  const confidence = assessConfidence(chain, atm);
  
  return {
    straddle: straddleMove,
    iv: ivMove,
    bands,
    confidence
  };
}

/**
 * Utility function to format expected move for display
 */
export function formatExpectedMove(result: ExpectedMoveResult, spot: number) {
  return {
    straddle: {
      display: `±$${result.straddle.abs.toFixed(2)} (${result.straddle.pct.toFixed(1)}%)`,
      confidence: result.confidence.straddle
    },
    iv: {
      display: `±$${result.iv.abs.toFixed(2)} (${result.iv.pct.toFixed(1)}%)`,
      confidence: result.confidence.iv
    },
    bands: {
      oneSigma: `$${result.bands.oneSigma.lower.toFixed(2)} - $${result.bands.oneSigma.upper.toFixed(2)}`,
      twoSigma: `$${result.bands.twoSigma.lower.toFixed(2)} - $${result.bands.twoSigma.upper.toFixed(2)}`
    }
  };
}
