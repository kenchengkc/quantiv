/**
 * Dolt Database Configuration
 * Configure your Dolt database connection details here
 */

export interface DoltDatabaseConfig {
  endpoint: string;
  database: string;
  branch?: string;
  apiKey?: string;
  endpoints: {
    chainEndpoint: string;
    ivEndpoint: string;
  };
  tables: {
    optionsChain: string;
    ivHistory: string;
    symbols: string;
  };
}

// Dolt database configuration based on discovered structure
export const doltConfig: DoltDatabaseConfig = {
  // DoltHub public API endpoint
  endpoint: process.env.DOLT_ENDPOINT || 'https://www.dolthub.com/api/v1alpha1',
  
  // Repository: post-no-preference/options
  database: process.env.DOLT_DATABASE || 'post-no-preference/options',
  
  // Branch: master
  branch: process.env.DOLT_BRANCH || 'master',
  
  // No API key required for public DoltHub repositories
  apiKey: process.env.DOLT_API_KEY,
  
  // Separate endpoints for different table types
  endpoints: {
    chainEndpoint: process.env.DOLT_CHAIN_ENDPOINT || 'https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master',
    ivEndpoint: process.env.DOLT_IV_ENDPOINT || 'https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master',
  },
  
  // Table names for different data types
  tables: {
    optionsChain: process.env.DOLT_OPTIONS_TABLE || 'option_chain', // Raw options data
    ivHistory: process.env.DOLT_IV_TABLE || 'volatility_history', // Dedicated IV/HV analytics
    symbols: process.env.DOLT_SYMBOLS_TABLE || 'volatility_history', // Extract symbols from volatility_history
  },
};

// Comprehensive live data configuration
export const comprehensiveConfig = {
  dolt: doltConfig,
  apis: {
    polygonApiKey: process.env.POLYGON_API_KEY,
    fmpApiKey: process.env.FMP_API_KEY || process.env.myFMP_API_KEY,
    alphaVantageApiKey: process.env.ALPHA_VANTAGE_API_KEY,
    finnhubApiKey: process.env.FINNHUB_API_KEY,
  },
};
