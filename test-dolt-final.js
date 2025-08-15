#!/usr/bin/env node

/**
 * Test Dolt integration with the correct single-table schema
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

async function testDoltIntegration() {
  console.log('🧪 Testing Dolt Integration with Correct Schema\n');

  // Test 1: Get available symbols (first 10)
  console.log('📋 Test 1: Available symbols');
  const symbols = await queryDolt('SELECT DISTINCT act_symbol FROM option_chain ORDER BY act_symbol LIMIT 10');
  if (symbols) {
    console.log('✅ Symbols:', symbols.map(r => r.act_symbol).join(', '));
  }

  // Test 2: Check if BLK exists
  console.log('\n📊 Test 2: BLK data availability');
  const blkCount = await queryDolt("SELECT COUNT(*) as count FROM option_chain WHERE act_symbol = 'BLK'");
  if (blkCount) {
    console.log(`✅ BLK has ${blkCount[0].count} records`);
  }

  // Test 3: BLK IV history for sparkline (last 30 days of data)
  console.log('\n📈 Test 3: BLK IV history for sparkline');
  const ivHistory = await queryDolt(`
    SELECT 
      date,
      AVG(vol) as avg_iv
    FROM option_chain 
    WHERE act_symbol = 'BLK'
      AND vol IS NOT NULL
    GROUP BY date
    ORDER BY date DESC
    LIMIT 10
  `);
  
  if (ivHistory) {
    console.log('✅ BLK IV history (last 10 days):');
    ivHistory.forEach(row => {
      console.log(`  ${row.date}: ${(parseFloat(row.avg_iv) * 100).toFixed(2)}% IV`);
    });
  }

  // Test 4: BLK IV stats (current, high, low for rank calculation)
  console.log('\n🎯 Test 4: BLK IV stats for rank calculation');
  const ivStats = await queryDolt(`
    SELECT 
      AVG(vol) as current_iv,
      MAX(vol) as high_52w,
      MIN(vol) as low_52w,
      COUNT(*) as data_points
    FROM option_chain 
    WHERE act_symbol = 'BLK'
      AND vol IS NOT NULL
      AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
  `);
  
  if (ivStats && ivStats[0]) {
    const stats = ivStats[0];
    const current = parseFloat(stats.current_iv) || 0;
    const high = parseFloat(stats.high_52w) || 0;
    const low = parseFloat(stats.low_52w) || 0;
    
    const range = high - low;
    const rank = range > 0 ? ((current - low) / range) * 100 : 50;
    
    console.log('✅ BLK IV Stats:');
    console.log(`  Current IV: ${(current * 100).toFixed(2)}%`);
    console.log(`  52W High: ${(high * 100).toFixed(2)}%`);
    console.log(`  52W Low: ${(low * 100).toFixed(2)}%`);
    console.log(`  IV Rank: ${Math.round(rank)}`);
    console.log(`  Data points: ${stats.data_points}`);
  }

  // Test 5: BLK options chain sample (recent data)
  console.log('\n⛓️ Test 5: BLK options chain sample');
  const chainSample = await queryDolt(`
    SELECT 
      date,
      expiration,
      strike,
      call_put,
      bid,
      ask,
      vol,
      delta
    FROM option_chain 
    WHERE act_symbol = 'BLK'
      AND vol IS NOT NULL
    ORDER BY date DESC, strike ASC
    LIMIT 5
  `);
  
  if (chainSample) {
    console.log('✅ BLK recent options:');
    chainSample.forEach(row => {
      console.log(`  ${row.date} | $${row.strike} ${row.call_put} | ${row.bid}-${row.ask} | IV: ${(parseFloat(row.vol) * 100).toFixed(1)}% | Δ: ${row.delta}`);
    });
  }

  console.log('\n🏁 Dolt integration test complete!');
  console.log('\n✅ Key findings:');
  console.log('- Single table `option_chain` contains all data');
  console.log('- Use `vol` column for IV calculations');
  console.log('- Group by `date` for IV history sparklines');
  console.log('- Use `act_symbol` for filtering by stock');
  console.log('- Both chain and IV endpoints should query the same table');
}

testDoltIntegration().catch(console.error);
