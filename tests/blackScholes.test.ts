import { describe, it, expect } from 'vitest';
import { blackScholes, impliedVolBrent, findATMStrike, type BSParams } from '../lib/pricing/blackScholes';

describe('Black-Scholes Pricing', () => {
  // Test case 1: Standard ATM option
  const testCase1: BSParams = {
    S: 100,    // Stock price
    K: 100,    // Strike price (ATM)
    T: 0.25,   // 3 months to expiration
    r: 0.05,   // 5% risk-free rate
    q: 0.02,   // 2% dividend yield
    iv: 0.20   // 20% implied volatility
  };

  // Test case 2: ITM Call / OTM Put
  const testCase2: BSParams = {
    S: 110,
    K: 100,
    T: 0.5,
    r: 0.03,
    q: 0.01,
    iv: 0.25
  };

  // Test case 3: OTM Call / ITM Put
  const testCase3: BSParams = {
    S: 90,
    K: 100,
    T: 0.1,
    r: 0.04,
    q: 0,
    iv: 0.30
  };

  // Test case 4: High volatility
  const testCase4: BSParams = {
    S: 100,
    K: 105,
    T: 0.75,
    r: 0.02,
    q: 0.015,
    iv: 0.50
  };

  // Test case 5: Near expiration
  const testCase5: BSParams = {
    S: 100,
    K: 100,
    T: 0.0274, // 10 days
    r: 0.05,
    q: 0,
    iv: 0.15
  };

  describe('Option Pricing', () => {
    it('should calculate correct prices for ATM options', () => {
      const result = blackScholes(testCase1);
      
      // ATM call and put should be roughly equal (adjusted for carry)
      expect(result.call).toBeGreaterThan(0);
      expect(result.put).toBeGreaterThan(0);
      expect(Math.abs(result.call - result.put)).toBeLessThan(2); // Within $2
    });

    it('should calculate correct prices for ITM call', () => {
      const result = blackScholes(testCase2);
      
      // ITM call should have higher intrinsic value
      const intrinsicCall = Math.max(0, testCase2.S - testCase2.K);
      expect(result.call).toBeGreaterThan(intrinsicCall);
      expect(result.call).toBeGreaterThan(result.put);
    });

    it('should calculate correct prices for ITM put', () => {
      const result = blackScholes(testCase3);
      
      // ITM put should have higher intrinsic value
      const intrinsicPut = Math.max(0, testCase3.K - testCase3.S);
      expect(result.put).toBeGreaterThan(intrinsicPut);
      expect(result.put).toBeGreaterThan(result.call);
    });

    it('should handle high volatility correctly', () => {
      const result = blackScholes(testCase4);
      
      // High volatility should increase option values
      expect(result.call).toBeGreaterThan(5);
      expect(result.put).toBeGreaterThan(5);
    });

    it('should handle near expiration correctly', () => {
      const result = blackScholes(testCase5);
      
      // Near expiration ATM options should have low time value
      expect(result.call).toBeLessThan(3);
      expect(result.put).toBeLessThan(3);
    });
  });

  describe('Greeks Calculation', () => {
    it('should calculate delta correctly', () => {
      const result = blackScholes(testCase1);
      
      // ATM delta should be around 0.5 for calls, -0.5 for puts
      expect(result.delta.call).toBeGreaterThan(0.4);
      expect(result.delta.call).toBeLessThan(0.6);
      expect(result.delta.put).toBeGreaterThan(-0.6);
      expect(result.delta.put).toBeLessThan(-0.4);
      
      // Call delta - Put delta should equal e^(-q*T) (put-call parity)
      const expectedDiff = Math.exp(-testCase1.q * testCase1.T);
      expect(Math.abs(result.delta.call - result.delta.put - expectedDiff)).toBeLessThan(0.001);
    });

    it('should calculate gamma correctly', () => {
      const result = blackScholes(testCase1);
      
      // Gamma should be positive and highest for ATM options
      expect(result.gamma).toBeGreaterThan(0);
      expect(result.gamma).toBeLessThan(0.1); // Reasonable upper bound
    });

    it('should calculate theta correctly', () => {
      const result = blackScholes(testCase1);
      
      // Theta should be negative (time decay)
      expect(result.theta.call).toBeLessThan(0);
      expect(result.theta.put).toBeLessThan(0);
    });

    it('should calculate vega correctly', () => {
      const result = blackScholes(testCase1);
      
      // Vega should be positive and same for calls and puts
      expect(result.vega).toBeGreaterThan(0);
      expect(result.vega).toBeLessThan(50); // Reasonable upper bound
    });

    it('should calculate rho correctly', () => {
      const result = blackScholes(testCase1);
      
      // Call rho should be positive, put rho should be negative
      expect(result.rho.call).toBeGreaterThan(0);
      expect(result.rho.put).toBeLessThan(0);
    });
  });

  describe('Error Handling', () => {
    it('should throw error for negative stock price', () => {
      expect(() => blackScholes({ ...testCase1, S: -100 })).toThrow();
    });

    it('should throw error for negative strike price', () => {
      expect(() => blackScholes({ ...testCase1, K: -100 })).toThrow();
    });

    it('should throw error for zero or negative time', () => {
      expect(() => blackScholes({ ...testCase1, T: 0 })).toThrow();
      expect(() => blackScholes({ ...testCase1, T: -0.1 })).toThrow();
    });

    it('should throw error for zero or negative volatility', () => {
      expect(() => blackScholes({ ...testCase1, iv: 0 })).toThrow();
      expect(() => blackScholes({ ...testCase1, iv: -0.1 })).toThrow();
    });
  });
});

describe('Implied Volatility Calculation', () => {
  it('should calculate implied volatility for ATM call', () => {
    const params = { S: 100, K: 100, T: 0.25, r: 0.05, q: 0.02 };
    const targetVol = 0.20;
    
    // Calculate theoretical price
    const theoreticalPrice = blackScholes({ ...params, iv: targetVol }).call;
    
    // Calculate implied volatility
    const impliedVol = impliedVolBrent(theoreticalPrice, params, true);
    
    expect(Math.abs(impliedVol - targetVol)).toBeLessThan(0.001);
  });

  it('should calculate implied volatility for ATM put', () => {
    const params = { S: 100, K: 100, T: 0.25, r: 0.05, q: 0.02 };
    const targetVol = 0.25;
    
    // Calculate theoretical price
    const theoreticalPrice = blackScholes({ ...params, iv: targetVol }).put;
    
    // Calculate implied volatility
    const impliedVol = impliedVolBrent(theoreticalPrice, params, false);
    
    expect(Math.abs(impliedVol - targetVol)).toBeLessThan(0.001);
  });

  it('should handle ITM options correctly', () => {
    const params = { S: 110, K: 100, T: 0.5, r: 0.03, q: 0.01 };
    const targetVol = 0.30;
    
    const theoreticalPrice = blackScholes({ ...params, iv: targetVol }).call;
    const impliedVol = impliedVolBrent(theoreticalPrice, params, true);
    
    expect(Math.abs(impliedVol - targetVol)).toBeLessThan(0.001);
  });

  it('should handle OTM options correctly', () => {
    const params = { S: 90, K: 100, T: 0.5, r: 0.03, q: 0.01 };
    const targetVol = 0.35;
    
    const theoreticalPrice = blackScholes({ ...params, iv: targetVol }).call;
    const impliedVol = impliedVolBrent(theoreticalPrice, params, true);
    
    expect(Math.abs(impliedVol - targetVol)).toBeLessThan(0.001);
  });

  it('should return minimum volatility for prices at intrinsic value', () => {
    const params = { S: 110, K: 100, T: 0.25, r: 0.05, q: 0 };
    const intrinsicValue = 10; // S - K
    
    const impliedVol = impliedVolBrent(intrinsicValue, params, true);
    expect(impliedVol).toBe(0.01);
  });

  it('should throw error for negative market price', () => {
    const params = { S: 100, K: 100, T: 0.25, r: 0.05, q: 0.02 };
    expect(() => impliedVolBrent(-5, params, true)).toThrow();
  });
});

describe('ATM Strike Finder', () => {
  it('should find exact ATM strike', () => {
    const strikes = [95, 100, 105, 110];
    const spot = 100;
    
    const atmStrike = findATMStrike(strikes, spot);
    expect(atmStrike).toBe(100);
  });

  it('should find closest strike when spot is between strikes', () => {
    const strikes = [95, 100, 105, 110];
    const spot = 102;
    
    const atmStrike = findATMStrike(strikes, spot);
    expect(atmStrike).toBe(100);
  });

  it('should find closest strike when spot is between strikes (other direction)', () => {
    const strikes = [95, 100, 105, 110];
    const spot = 103;
    
    const atmStrike = findATMStrike(strikes, spot);
    expect(atmStrike).toBe(105);
  });

  it('should handle edge cases', () => {
    const strikes = [90, 95, 100];
    const spot = 92;
    
    const atmStrike = findATMStrike(strikes, spot);
    expect(atmStrike).toBe(90);
  });

  it('should throw error for empty strikes array', () => {
    expect(() => findATMStrike([], 100)).toThrow();
  });
});
