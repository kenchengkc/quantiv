#!/usr/bin/env node

/**
 * Test Dolt integration with correct two-table structure
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

async function testDoltTables() {
  console.log('🧪 Testing Dolt Two-Table Structure\n');

  // Test 1: Check volatility_history table for IV analytics
  console.log('📊 Test 1: volatility_history table - IV Analytics');
  const ivSymbols = await queryDolt('SELECT DISTINCT act_symbol FROM volatility_history ORDER BY act_symbol LIMIT 10');
  if (ivSymbols) {
    console.log('✅ IV symbols:', ivSymbols.map(r => r.act_symbol).join(', '));
  }

  // Test 2: Check option_chain table for options data
  console.log('\n⛓️ Test 2: option_chain table - Options Data');
  const chainSymbols = await queryDolt('SELECT DISTINCT act_symbol FROM option_chain ORDER BY act_symbol LIMIT 10');
  if (chainSymbols) {
    console.log('✅ Chain symbols:', chainSymbols.map(r => r.act_symbol).join(', '));
  }

  // Test 3: BLK IV data from volatility_history (for sparklines)
  console.log('\n📈 Test 3: BLK IV History from volatility_history');
  const blkIV = await queryDolt(`
    SELECT 
      date,
      iv_current,
      iv_year_high,
      iv_year_low
    FROM volatility_history 
    WHERE act_symbol = 'BLK'
    ORDER BY date DESC
    LIMIT 5
  `);
  
  if (blkIV) {
    console.log('✅ BLK IV History:');
    blkIV.forEach(row => {
      const current = (parseFloat(row.iv_current) * 100).toFixed(2);
      const high = (parseFloat(row.iv_year_high) * 100).toFixed(2);
      const low = (parseFloat(row.iv_year_low) * 100).toFixed(2);
      console.log(`  ${row.date}: IV ${current}% (Range: ${low}%-${high}%)`);
    });
  }

  // Test 4: BLK IV Rank calculation
  console.log('\n🎯 Test 4: BLK IV Rank from volatility_history');
  const blkRank = await queryDolt(`
    SELECT 
      iv_current,
      iv_year_high,
      iv_year_low,
      hv_current
    FROM volatility_history 
    WHERE act_symbol = 'BLK'
    ORDER BY date DESC
    LIMIT 1
  `);
  
  if (blkRank && blkRank[0]) {
    const data = blkRank[0];
    const current = parseFloat(data.iv_current) || 0;
    const high = parseFloat(data.iv_year_high) || 0;
    const low = parseFloat(data.iv_year_low) || 0;
    const hv = parseFloat(data.hv_current) || 0;
    
    const range = high - low;
    const rank = range > 0 ? ((current - low) / range) * 100 : 50;
    
    console.log('✅ BLK IV Rank:');
    console.log(`  Current IV: ${(current * 100).toFixed(2)}%`);
    console.log(`  Current HV: ${(hv * 100).toFixed(2)}%`);
    console.log(`  Year High: ${(high * 100).toFixed(2)}%`);
    console.log(`  Year Low: ${(low * 100).toFixed(2)}%`);
    console.log(`  IV Rank: ${Math.round(rank)}`);
  }

  // Test 5: BLK options chain from option_chain table
  console.log('\n⛓️ Test 5: BLK Options Chain from option_chain');
  const blkChain = await queryDolt(`
    SELECT 
      date,
      expiration,
      strike,
      call_put,
      bid,
      ask,
      vol
    FROM option_chain 
    WHERE act_symbol = 'BLK'
    ORDER BY date DESC, strike ASC
    LIMIT 5
  `);
  
  if (blkChain) {
    console.log('✅ BLK Options Chain:');
    blkChain.forEach(row => {
      console.log(`  ${row.date} | $${row.strike} ${row.call_put} | ${row.bid}-${row.ask} | IV: ${(parseFloat(row.vol) * 100).toFixed(1)}%`);
    });
  }

  console.log('\n🏁 Two-table structure test complete!');
  console.log('\n✅ Summary:');
  console.log('- volatility_history: Professional IV/HV analytics with pre-calculated ranks');
  console.log('- option_chain: Raw options data with strikes, Greeks, and individual option IVs');
  console.log('- Both tables have act_symbol and date columns for joining');
  console.log('- Use volatility_history for IV sparklines and rank calculations');
  console.log('- Use option_chain for detailed options chain display');
}

testDoltTables().catch(console.error);
