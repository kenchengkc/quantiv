/**
 * Dolt Database Connection Test
 * Run this script to test your Dolt database connection and explore your data structure
 */

require('dotenv').config({ path: '.env.local' });

async function testDoltConnection() {
  console.log('🔍 Testing Dolt Database Connection\n');

  // Configuration - UPDATE THESE VALUES WITH YOUR ACTUAL DOLT DETAILS
  const config = {
    endpoint: process.env.DOLT_ENDPOINT || 'https://www.dolthub.com/api/v1alpha1',
    database: process.env.DOLT_DATABASE || 'your-database-name',
    branch: process.env.DOLT_BRANCH || 'main',
    apiKey: process.env.DOLT_API_KEY, // Optional
  };

  console.log('📋 Configuration:');
  console.log(`  Endpoint: ${config.endpoint}`);
  console.log(`  Database: ${config.database}`);
  console.log(`  Branch: ${config.branch}`);
  console.log(`  API Key: ${config.apiKey ? 'Configured ✅' : 'Not configured ❌'}\n`);

  try {
    // Test basic connection by listing tables
    console.log('🔗 Testing connection...');
    const tablesResult = await executeDoltQuery(config, 'SHOW TABLES');
    
    if (tablesResult.length === 0) {
      console.log('❌ No tables found or connection failed');
      return;
    }

    console.log('✅ Connection successful!');
    console.log(`📊 Found ${tablesResult.length} tables:`);
    tablesResult.forEach((table, index) => {
      console.log(`  ${index + 1}. ${Object.values(table)[0]}`);
    });

    // Test options chain data
    console.log('\n🔍 Testing options chain data...');
    const optionsTest = await testOptionsData(config);
    
    // Test IV history data
    console.log('\n📈 Testing IV history data...');
    const ivTest = await testIVData(config);

    // Test symbol availability
    console.log('\n📋 Testing symbol availability...');
    const symbolsTest = await testSymbolData(config);

    console.log('\n✅ Dolt Database Test Complete!');
    console.log('\n📋 Summary:');
    console.log(`  Tables: ${tablesResult.length} found`);
    console.log(`  Options Data: ${optionsTest ? '✅' : '❌'}`);
    console.log(`  IV Data: ${ivTest ? '✅' : '❌'}`);
    console.log(`  Symbols: ${symbolsTest ? '✅' : '❌'}`);

  } catch (error) {
    console.error('❌ Dolt connection test failed:', error.message);
    console.log('\n💡 Troubleshooting:');
    console.log('  1. Check your DOLT_ENDPOINT, DOLT_DATABASE, and DOLT_BRANCH in .env.local');
    console.log('  2. Verify your database is accessible and has the correct permissions');
    console.log('  3. If using authentication, ensure DOLT_API_KEY is set correctly');
  }
}

async function executeDoltQuery(config, sql) {
  const url = `${config.endpoint}/${config.database}/${config.branch}`;
  
  const headers = {
    'Content-Type': 'application/json',
  };

  if (config.apiKey) {
    headers['Authorization'] = `Bearer ${config.apiKey}`;
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      query: sql,
    }),
  });

  if (!response.ok) {
    throw new Error(`Dolt API error: ${response.status} ${response.statusText}`);
  }

  const result = await response.json();
  return result.rows || [];
}

async function testOptionsData(config) {
  try {
    // Try common table names for options data
    const possibleTables = ['options_chain', 'options', 'option_chain', 'chains'];
    
    for (const tableName of possibleTables) {
      try {
        const result = await executeDoltQuery(config, `SELECT * FROM ${tableName} LIMIT 5`);
        if (result.length > 0) {
          console.log(`  ✅ Found options data in table: ${tableName}`);
          console.log(`  📊 Sample columns:`, Object.keys(result[0]));
          console.log(`  📈 Sample row:`, result[0]);
          return true;
        }
      } catch (error) {
        // Table doesn't exist, continue
      }
    }
    
    console.log('  ❌ No options chain data found');
    return false;
  } catch (error) {
    console.log('  ❌ Error testing options data:', error.message);
    return false;
  }
}

async function testIVData(config) {
  try {
    // Try common table names for IV data
    const possibleTables = ['iv_history', 'implied_volatility', 'iv_data', 'volatility'];
    
    for (const tableName of possibleTables) {
      try {
        const result = await executeDoltQuery(config, `SELECT * FROM ${tableName} LIMIT 5`);
        if (result.length > 0) {
          console.log(`  ✅ Found IV data in table: ${tableName}`);
          console.log(`  📊 Sample columns:`, Object.keys(result[0]));
          console.log(`  📈 Sample row:`, result[0]);
          return true;
        }
      } catch (error) {
        // Table doesn't exist, continue
      }
    }
    
    console.log('  ❌ No IV history data found');
    return false;
  } catch (error) {
    console.log('  ❌ Error testing IV data:', error.message);
    return false;
  }
}

async function testSymbolData(config) {
  try {
    // Try to find symbols from any table
    const queries = [
      'SELECT DISTINCT symbol FROM options_chain LIMIT 10',
      'SELECT DISTINCT symbol FROM options LIMIT 10',
      'SELECT DISTINCT symbol FROM iv_history LIMIT 10',
      'SELECT DISTINCT symbol FROM symbols LIMIT 10',
    ];
    
    for (const query of queries) {
      try {
        const result = await executeDoltQuery(config, query);
        if (result.length > 0) {
          console.log(`  ✅ Found ${result.length} symbols`);
          console.log(`  📋 Sample symbols:`, result.slice(0, 5).map(r => r.symbol).join(', '));
          return true;
        }
      } catch (error) {
        // Query failed, continue
      }
    }
    
    console.log('  ❌ No symbols found');
    return false;
  } catch (error) {
    console.log('  ❌ Error testing symbols:', error.message);
    return false;
  }
}

testDoltConnection();
