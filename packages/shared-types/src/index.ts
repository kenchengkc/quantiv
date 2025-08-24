// Shared TypeScript types for Quantiv

export interface OptionContract {
  symbol: string;
  strike: number;
  expiry: Date;
  type: 'call' | 'put';
}

export interface OptionsChain {
  symbol: string;
  expiry: Date;
  calls: OptionContract[];
  puts: OptionContract[];
}

export interface ExpectedMove {
  symbol: string;
  expiry: Date;
  movePercent: number;
  moveAbsolute: number;
  confidence: 'high' | 'medium' | 'low';
}

// Add more shared types as needed
