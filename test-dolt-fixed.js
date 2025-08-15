#!/usr/bin/env node

/**
 * Test script to verify the fixed Dolt integration
 */

const { doltConfig } = require('./lib/config/doltConfig');

async function testDoltIntegration() {
  console.log('🧪 Testing Fixed Dolt Integration\n');

  // Test 1: Basic connection and symbol availability
  console.log('📋 Test 1: Check available symbols');
  try {
    const url = `${doltConfig.endpoints.chainEndpoint}?q=${encodeURIComponent('SELECT DISTINCT act_symbol FROM option_chain LIMIT 10')}`;
    console.log('URL:', url);
    
    const response = await fetch(url);
    const result = await response.json();
    
    if (result.query_execution_status === 'Success') {
      console.log('✅ Symbols found:', result.rows.map(r => r.act_symbol).join(', '));
    } else {
      console.log('❌ Query failed:', result.query_execution_message);
    }
  } catch (error) {
    console.log('❌ Connection failed:', error.message);
  }

  console.log('\n📊 Test 2: Check BLK data availability');
  try {
    const url = `${doltConfig.endpoints.chainEndpoint}?q=${encodeURIComponent("SELECT COUNT(*) as count FROM option_chain WHERE act_symbol = 'BLK'")}`;
    
    const response = await fetch(url);
    const result = await response.json();
    
    if (result.query_execution_status === 'Success') {
      const count = result.rows[0]?.count || 0;
      console.log(`✅ BLK has ${count} option records`);
    } else {
      console.log('❌ Query failed:', result.query_execution_message);
    }
  } catch (error) {
    console.log('❌ Query failed:', error.message);
  }

  console.log('\n📈 Test 3: Check BLK IV data for sparkline');
  try {
    const sql = `
      SELECT 
        date,
        AVG(vol) as avg_iv
      FROM option_chain 
      WHERE act_symbol = 'BLK'
        AND date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
        AND vol IS NOT NULL
      GROUP BY date
      ORDER BY date ASC
      LIMIT 5
    `;
    
    const url = `${doltConfig.endpoints.ivEndpoint}?q=${encodeURIComponent(sql)}`;
    
    const response = await fetch(url);
    const result = await response.json();
    
    if (result.query_execution_status === 'Success') {
      console.log(`✅ BLK IV history: ${result.rows.length} data points`);
      result.rows.forEach(row => {
        console.log(`  ${row.date}: ${(parseFloat(row.avg_iv) * 100).toFixed(2)}% IV`);
      });
    } else {
      console.log('❌ Query failed:', result.query_execution_message);
    }
  } catch (error) {
    console.log('❌ Query failed:', error.message);
  }

  console.log('\n🎯 Test 4: Check BLK IV stats calculation');
  try {
    const sql = `
      SELECT 
        AVG(vol) as current_iv,
        MAX(vol) as high_52w,
        MIN(vol) as low_52w
      FROM option_chain 
      WHERE act_symbol = 'BLK'
        AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
        AND vol IS NOT NULL
    `;
    
    const url = `${doltConfig.endpoints.ivEndpoint}?q=${encodeURIComponent(sql)}`;
    
    const response = await fetch(url);
    const result = await response.json();
    
    if (result.query_execution_status === 'Success' && result.rows.length > 0) {
      const row = result.rows[0];
      const current = parseFloat(row.current_iv) || 0;
      const high = parseFloat(row.high_52w) || 0;
      const low = parseFloat(row.low_52w) || 0;
      
      const range = high - low;
      const rank = range > 0 ? ((current - low) / range) * 100 : 50;
      
      console.log('✅ BLK IV Stats:');
      console.log(`  Current IV: ${(current * 100).toFixed(2)}%`);
      console.log(`  52W High: ${(high * 100).toFixed(2)}%`);
      console.log(`  52W Low: ${(low * 100).toFixed(2)}%`);
      console.log(`  IV Rank: ${Math.round(rank)}`);
    } else {
      console.log('❌ Query failed:', result.query_execution_message);
    }
  } catch (error) {
    console.log('❌ Query failed:', error.message);
  }

  console.log('\n🏁 Dolt Integration Test Complete');
}

// Run the test
testDoltIntegration().catch(console.error);
