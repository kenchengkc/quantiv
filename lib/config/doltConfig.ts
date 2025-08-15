/**
 * Dolt Database Configuration
 * Configure your Dolt database connection details here
 */

export interface DoltDatabaseConfig {
  endpoint: string;
  database: string;
  branch?: string;
  apiKey?: string;
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
  
  // Table names discovered in the database
  tables: {
    optionsChain: process.env.DOLT_OPTIONS_TABLE || 'option_chain',
    ivHistory: process.env.DOLT_IV_TABLE || 'option_chain', // Use same table for IV history
    symbols: process.env.DOLT_SYMBOLS_TABLE || 'option_chain', // Extract symbols from option_chain
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
