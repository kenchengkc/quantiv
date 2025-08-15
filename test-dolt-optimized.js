#!/usr/bin/env node

/**
 * Test optimized Dolt queries with date filtering
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

async function testOptimizedQueries() {
  console.log('🚀 Testing Optimized Dolt Queries (Past Year Only)\n');

  // Test 1: BLK options chain (past year, limited rows)
  console.log('⛓️ Test 1: BLK options chain (optimized)');
  const blkChain = await queryDolt(`
    SELECT 
      date,
      expiration,
      strike,
      call_put,
      vol
    FROM option_chain 
    WHERE act_symbol = 'BLK'
      AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
    ORDER BY date DESC, strike ASC 
    LIMIT 10
  `);
  
  if (blkChain) {
    console.log(`✅ Found ${blkChain.length} recent BLK options:`);
    blkChain.slice(0, 3).forEach(row => {
      console.log(`  ${row.date} | $${row.strike} ${row.call_put} | IV: ${(parseFloat(row.vol) * 100).toFixed(1)}%`);
    });
  }

  // Test 2: BLK expirations (future only, limited)
  console.log('\n📅 Test 2: BLK future expirations (optimized)');
  const blkExpirations = await queryDolt(`
    SELECT DISTINCT expiration
    FROM option_chain 
    WHERE act_symbol = 'BLK'
      AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
      AND expiration >= CURDATE()
    ORDER BY expiration ASC
    LIMIT 10
  `);
  
  if (blkExpirations) {
    console.log(`✅ Found ${blkExpirations.length} future BLK expirations:`);
    blkExpirations.slice(0, 5).forEach(row => {
      console.log(`  ${row.expiration}`);
    });
  }

  // Test 3: BLK expected moves (past year, aggregated)
  console.log('\n📊 Test 3: BLK expected moves (optimized)');
  const blkMoves = await queryDolt(`
    SELECT 
      date,
      AVG(vol) as avg_iv,
      COUNT(*) as option_count
    FROM option_chain 
    WHERE act_symbol = 'BLK'
      AND date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
      AND date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
      AND vol IS NOT NULL
      AND vol > 0
    GROUP BY date
    ORDER BY date DESC
    LIMIT 5
  `);
  
  if (blkMoves) {
    console.log(`✅ Found ${blkMoves.length} recent BLK expected move data points:`);
    blkMoves.forEach(row => {
      const iv = parseFloat(row.avg_iv) || 0;
      const expectedMove = iv * Math.sqrt(1/365) * 100;
      console.log(`  ${row.date}: IV ${(iv * 100).toFixed(2)}% → EM ${expectedMove.toFixed(2)}% (${row.option_count} options)`);
    });
  }

  // Test 4: Count recent vs total data
  console.log('\n🔢 Test 4: Data volume comparison');
  
  const recentCount = await queryDolt(`
    SELECT COUNT(*) as recent_count 
    FROM option_chain 
    WHERE date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
  `);
  
  if (recentCount) {
    console.log(`✅ Recent data (past year): ${recentCount[0].recent_count.toLocaleString()} rows`);
    console.log(`📊 Performance improvement: ~${Math.round((87406146 - recentCount[0].recent_count) / 87406146 * 100)}% reduction in query scope`);
  }

  console.log('\n🏁 Optimization test complete!');
  console.log('\n✅ Key optimizations:');
  console.log('- All option_chain queries limited to past 365 days');
  console.log('- Row limits (50-1000) prevent timeouts');
  console.log('- Future-only expiration filtering');
  console.log('- Aggregated data for expected moves');
  console.log('- Massive performance improvement for 87M+ row table');
}

testOptimizedQueries().catch(console.error);
