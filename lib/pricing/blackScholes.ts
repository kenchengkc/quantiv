/**
 * Black-Scholes pricing model with Greeks calculation
 * Used for options pricing and implied volatility calculations
 */

export interface BSParams {
  S: number;  // Current stock price
  K: number;  // Strike price
  T: number;  // Time to expiration (in years)
  r: number;  // Risk-free rate
  q: number;  // Dividend yield (carry rate)
  iv: number; // Implied volatility
}

export interface BSResult {
  call: number;
  put: number;
  delta: { call: number; put: number };
  gamma: number;
  theta: { call: number; put: number };
  vega: number;
  rho: { call: number; put: number };
}

/**
 * Standard normal cumulative distribution function
 */
function normCDF(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;

  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.sqrt(2.0);

  const t = 1.0 / (1.0 + p * x);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);

  return 0.5 * (1.0 + sign * y);
}

/**
 * Standard normal probability density function
 */
function normPDF(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

/**
 * Calculate d1 and d2 parameters for Black-Scholes
 */
function calculateD1D2(params: BSParams): { d1: number; d2: number } {
  const { S, K, T, r, q, iv } = params;
  
  if (T <= 0 || iv <= 0) {
    throw new Error('Time to expiration and volatility must be positive');
  }

  const d1 = (Math.log(S / K) + (r - q + 0.5 * iv * iv) * T) / (iv * Math.sqrt(T));
  const d2 = d1 - iv * Math.sqrt(T);

  return { d1, d2 };
}

/**
 * Calculate Black-Scholes option prices and Greeks
 */
export function blackScholes(params: BSParams): BSResult {
  const { S, K, T, r, q } = params;
  
  if (S <= 0 || K <= 0) {
    throw new Error('Stock price and strike price must be positive');
  }

  const { d1, d2 } = calculateD1D2(params);
  
  const Nd1 = normCDF(d1);
  const Nd2 = normCDF(d2);
  const NegD1 = normCDF(-d1);
  const NegD2 = normCDF(-d2);
  const nd1 = normPDF(d1);

  const discountFactor = Math.exp(-r * T);
  const dividendFactor = Math.exp(-q * T);

  // Option prices
  const call = S * dividendFactor * Nd1 - K * discountFactor * Nd2;
  const put = K * discountFactor * NegD2 - S * dividendFactor * NegD1;

  // Greeks
  const delta = {
    call: dividendFactor * Nd1,
    put: dividendFactor * (Nd1 - 1)
  };

  const gamma = (dividendFactor * nd1) / (S * params.iv * Math.sqrt(T));
  
  const theta = {
    call: (-S * dividendFactor * nd1 * params.iv / (2 * Math.sqrt(T)) 
           - r * K * discountFactor * Nd2 
           + q * S * dividendFactor * Nd1) / 365,
    put: (-S * dividendFactor * nd1 * params.iv / (2 * Math.sqrt(T)) 
          + r * K * discountFactor * NegD2 
          - q * S * dividendFactor * NegD1) / 365
  };

  const vega = (S * dividendFactor * nd1 * Math.sqrt(T)) / 100;

  const rho = {
    call: (K * T * discountFactor * Nd2) / 100,
    put: (-K * T * discountFactor * NegD2) / 100
  };

  return {
    call: Math.max(0, call),
    put: Math.max(0, put),
    delta,
    gamma,
    theta,
    vega,
    rho
  };
}

/**
 * Calculate implied volatility using Brent's method
 */
export function impliedVolBrent(
  marketPrice: number,
  params: Omit<BSParams, 'iv'>,
  isCall: boolean = true,
  tolerance: number = 1e-6,
  maxIterations: number = 100
): number {
  if (marketPrice <= 0) {
    throw new Error('Market price must be positive');
  }

  const { S, K, T } = params;
  
  // Intrinsic value bounds
  const intrinsic = isCall ? Math.max(0, S - K) : Math.max(0, K - S);
  if (marketPrice <= intrinsic) {
    return 0.01; // Minimum volatility
  }

  // Initial bounds for volatility search
  let volLow = 0.01;
  let volHigh = 5.0;

  // Test bounds
  const testLow = blackScholes({ ...params, iv: volLow });
  const testHigh = blackScholes({ ...params, iv: volHigh });
  
  const priceLow = isCall ? testLow.call : testLow.put;
  const priceHigh = isCall ? testHigh.call : testHigh.put;

  if (marketPrice < priceLow) return volLow;
  if (marketPrice > priceHigh) return volHigh;

  // Brent's method
  let a = volLow;
  let b = volHigh;
  let c = volHigh;
  
  let fa = priceLow - marketPrice;
  let fb = priceHigh - marketPrice;
  let fc = fb;

  for (let iter = 0; iter < maxIterations; iter++) {
    if (Math.abs(fb) < tolerance) {
      return b;
    }

    if (Math.sign(fa) === Math.sign(fb)) {
      a = c;
      fa = fc;
    }

    if (Math.abs(fa) < Math.abs(fb)) {
      [a, b] = [b, a];
      [fa, fb] = [fb, fa];
    }

    const tol = 2 * tolerance * Math.abs(b) + tolerance;
    const m = (a - b) / 2;

    if (Math.abs(m) < tol) {
      return b;
    }

    let p, q, r, s;
    if (Math.abs(c - b) < tolerance || Math.abs(fc - fb) < tolerance) {
      // Bisection
      p = m;
      q = 1;
    } else {
      // Inverse quadratic interpolation
      s = fb / fc;
      if (Math.abs(a - c) < tolerance) {
        p = 2 * m * s;
        q = 1 - s;
      } else {
        q = fc / fa;
        r = fb / fa;
        p = s * (2 * m * q * (q - r) - (b - c) * (r - 1));
        q = (q - 1) * (r - 1) * (s - 1);
      }
    }

    if (p > 0) q = -q;
    else p = -p;

    if (2 * p < Math.min(3 * m * q - Math.abs(tol * q), Math.abs((c - b) * q))) {
      c = b;
      fc = fb;
      b += p / q;
    } else {
      c = b;
      fc = fb;
      b += m;
    }

    const bsResult = blackScholes({ ...params, iv: b });
    fb = (isCall ? bsResult.call : bsResult.put) - marketPrice;
  }

  return b;
}

/**
 * Find at-the-money strike and calculate ATM implied volatility
 */
export function findATMStrike(strikes: number[], spot: number): number {
  if (strikes.length === 0) {
    throw new Error('No strikes provided');
  }

  return strikes.reduce((closest, strike) => 
    Math.abs(strike - spot) < Math.abs(closest - spot) ? strike : closest
  );
}
