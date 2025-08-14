const FMP_API_KEY = 'PAiqgIUMN0lx3YWPKphcSg9huH1hCOeS';
const POLYGON_API_KEY = '0e_7ksMurpISvT2JppKdcFQu6aZQdTJy';
const FMP_BASE_URL = 'https://financialmodelingprep.com/api/v3';
const POLYGON_BASE_URL = 'https://api.polygon.io';

async function testIntegratedAPIs() {
  console.log('Testing FMP + Polygon API integration for BLK...\n');
  
  // Test 1: FMP Quote data (what we actually use)
  try {
    console.log('1. Testing FMP Quote API (✅ Used in production):');
    const quoteResponse = await fetch(`${FMP_BASE_URL}/quote/BLK?apikey=${FMP_API_KEY}`);
    console.log(`Status: ${quoteResponse.status}`);
    
    if (quoteResponse.ok) {
      const quoteData = await quoteResponse.json();
      const quote = quoteData[0];
      console.log('Quote data:', {
        symbol: quote.symbol,
        name: quote.name,
        price: quote.price,
        change: quote.change,
        changePercent: quote.changesPercentage,
        volume: quote.volume
      });
    } else {
      const errorText = await quoteResponse.text();
      console.log('Quote error:', errorText);
    }
  } catch (error) {
    console.log('Quote fetch error:', error.message);
  }
  
  console.log('\n' + '='.repeat(60) + '\n');
  
  // Test 2: FMP Options (should fail - not supported)
  try {
    console.log('2. Testing FMP Options API (❌ Not supported - should fail):');
    const optionsResponse = await fetch(`${FMP_BASE_URL}/options/BLK?apikey=${FMP_API_KEY}`);
    console.log(`Status: ${optionsResponse.status}`);
    
    if (optionsResponse.ok) {
      const optionsData = await optionsResponse.json();
      console.log('Unexpected success:', optionsData);
    } else {
      const errorText = await optionsResponse.text();
      console.log('Expected error (FMP has no options):', errorText.substring(0, 200));
    }
  } catch (error) {
    console.log('Expected error:', error.message);
  }
  
  console.log('\n' + '='.repeat(60) + '\n');
  
  // Test 3: Polygon Options Contracts (what we actually use)
  try {
    console.log('3. Testing Polygon Options Contracts API (✅ Used in production):');
    const contractsResponse = await fetch(
      `${POLYGON_BASE_URL}/v3/reference/options/contracts?underlying_ticker=BLK&limit=10&apikey=${POLYGON_API_KEY}`
    );
    console.log(`Status: ${contractsResponse.status}`);
    
    if (contractsResponse.ok) {
      const contractsData = await contractsResponse.json();
      if (contractsData.results && contractsData.results.length > 0) {
        console.log('Sample contracts:', contractsData.results.slice(0, 3).map(contract => ({
          ticker: contract.ticker,
          strike: contract.strike_price,
          expiration: contract.expiration_date,
          type: contract.contract_type
        })));
        console.log(`Total contracts found: ${contractsData.results.length}`);
      } else {
        console.log('No contracts found');
      }
    } else {
      const errorText = await contractsResponse.text();
      console.log('Contracts error:', errorText);
    }
  } catch (error) {
    console.log('Contracts fetch error:', error.message);
  }
  
  console.log('\n' + '='.repeat(60) + '\n');
  
  // Test 4: Polygon Options Market Data
  try {
    console.log('4. Testing Polygon Options Market Data (✅ Used for pricing):');
    // First get a contract ticker
    const contractsResponse = await fetch(
      `${POLYGON_BASE_URL}/v3/reference/options/contracts?underlying_ticker=BLK&limit=1&apikey=${POLYGON_API_KEY}`
    );
    
    if (contractsResponse.ok) {
      const contractsData = await contractsResponse.json();
      if (contractsData.results && contractsData.results.length > 0) {
        const sampleTicker = contractsData.results[0].ticker;
        console.log(`Testing market data for: ${sampleTicker}`);
        
        const marketResponse = await fetch(
          `${POLYGON_BASE_URL}/v3/snapshot/options/${sampleTicker}?apikey=${POLYGON_API_KEY}`
        );
        
        if (marketResponse.ok) {
          const marketData = await marketResponse.json();
          console.log('Market data sample:', {
            bid: marketData.results?.bid,
            ask: marketData.results?.ask,
            last: marketData.results?.last_trade?.price,
            volume: marketData.results?.volume,
            implied_volatility: marketData.results?.implied_volatility
          });
        } else {
          console.log('Market data not available for this contract');
        }
      }
    }
  } catch (error) {
    console.log('Market data fetch error:', error.message);
  }
  
  console.log('\n' + '='.repeat(60) + '\n');
  console.log('✅ Integration Summary:');
  console.log('- FMP: Provides quotes, earnings, fundamentals');
  console.log('- Polygon: Provides options contracts, market data, Greeks');
  console.log('- This combination gives us complete market data coverage!');
}

testIntegratedAPIs().catch(console.error);
