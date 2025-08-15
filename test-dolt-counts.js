#!/usr/bin/env node

/**
 * Query Dolt database to get exact row counts and data scale
 */

const BASE_URL = 'https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master';

async function queryDolt(sql) {
  try {
    const url = `${BASE_URL}?q=${encodeURIComponent(sql)}`;
    const response = await fetch(url);
    const result = await response.json();
    
    if (result.query_execution_status === 'Success') {
      return result.rows;
    } else {
      console.log(`❌ Query failed: ${result.query_execution_message}`);
      return null;
    }
  } catch (error) {
    console.log(`❌ Query error: ${error.message}`);
    return null;
  }
}

async function getDoltDataCounts() {
  console.log('📊 Querying Dolt Database Row Counts\n');

  // Count rows in volatility_history table
  console.log('🔍 Counting volatility_history table...');
  const ivCount = await queryDolt('SELECT COUNT(*) as total_rows FROM volatility_history');
  if (ivCount) {
    console.log(`✅ volatility_history: ${ivCount[0].total_rows.toLocaleString()} rows`);
  }

  // Count unique symbols in volatility_history
  console.log('\n🔍 Counting unique symbols in volatility_history...');
  const ivSymbols = await queryDolt('SELECT COUNT(DISTINCT act_symbol) as unique_symbols FROM volatility_history');
  if (ivSymbols) {
    console.log(`✅ volatility_history unique symbols: ${ivSymbols[0].unique_symbols.toLocaleString()}`);
  }

  // Get date range for volatility_history
  console.log('\n🔍 Getting date range for volatility_history...');
  const ivDateRange = await queryDolt('SELECT MIN(date) as earliest, MAX(date) as latest FROM volatility_history');
  if (ivDateRange) {
    console.log(`✅ volatility_history date range: ${ivDateRange[0].earliest} to ${ivDateRange[0].latest}`);
  }

  // Count rows in option_chain table (with timeout protection)
  console.log('\n🔍 Counting option_chain table (this may take a moment)...');
  try {
    const chainCount = await Promise.race([
      queryDolt('SELECT COUNT(*) as total_rows FROM option_chain'),
      new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), 30000))
    ]);
    if (chainCount) {
      console.log(`✅ option_chain: ${chainCount[0].total_rows.toLocaleString()} rows`);
    }
  } catch (error) {
    if (error.message === 'Timeout') {
      console.log('⏰ option_chain count query timed out (likely very large dataset)');
      
      // Try a sample count instead
      console.log('🔍 Trying sample count from option_chain...');
      const sampleCount = await queryDolt('SELECT COUNT(*) as sample_rows FROM option_chain LIMIT 100000');
      if (sampleCount) {
        console.log(`✅ option_chain sample (first 100k): ${sampleCount[0].sample_rows.toLocaleString()} rows`);
      }
    } else {
      console.log(`❌ option_chain count failed: ${error.message}`);
    }
  }

  // Count unique symbols in option_chain (with limit)
  console.log('\n🔍 Counting unique symbols in option_chain...');
  const chainSymbols = await queryDolt('SELECT COUNT(DISTINCT act_symbol) as unique_symbols FROM option_chain');
  if (chainSymbols) {
    console.log(`✅ option_chain unique symbols: ${chainSymbols[0].unique_symbols.toLocaleString()}`);
  }

  // Get date range for option_chain
  console.log('\n🔍 Getting date range for option_chain...');
  const chainDateRange = await queryDolt('SELECT MIN(date) as earliest, MAX(date) as latest FROM option_chain');
  if (chainDateRange) {
    console.log(`✅ option_chain date range: ${chainDateRange[0].earliest} to ${chainDateRange[0].latest}`);
  }

  // Sample some data from both tables
  console.log('\n📋 Sample data from volatility_history:');
  const ivSample = await queryDolt(`
    SELECT act_symbol, date, iv_current, hv_current 
    FROM volatility_history 
    WHERE act_symbol IN ('AAPL', 'MSFT', 'GOOGL', 'TSLA', 'BLK')
    ORDER BY date DESC 
    LIMIT 5
  `);
  if (ivSample) {
    ivSample.forEach(row => {
      console.log(`  ${row.act_symbol} | ${row.date} | IV: ${(parseFloat(row.iv_current) * 100).toFixed(2)}% | HV: ${(parseFloat(row.hv_current) * 100).toFixed(2)}%`);
    });
  }

  console.log('\n📋 Sample data from option_chain:');
  const chainSample = await queryDolt(`
    SELECT act_symbol, date, strike, call_put, vol 
    FROM option_chain 
    WHERE act_symbol IN ('AAPL', 'MSFT') 
    AND vol IS NOT NULL
    ORDER BY date DESC 
    LIMIT 5
  `);
  if (chainSample) {
    chainSample.forEach(row => {
      console.log(`  ${row.act_symbol} | ${row.date} | $${row.strike} ${row.call_put} | IV: ${(parseFloat(row.vol) * 100).toFixed(2)}%`);
    });
  }

  console.log('\n🏁 Data scale analysis complete!');
}

getDoltDataCounts().catch(console.error);
