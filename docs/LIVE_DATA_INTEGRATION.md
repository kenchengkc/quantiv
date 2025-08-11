# Live Data Integration Guide

## Overview

Quantiv now supports **real-time financial data** from multiple professional APIs, providing live options chains, earnings calendars, and expected move calculations for all ticker dashboards.

## Data Sources

### Primary APIs
1. **Polygon.io** - Premium options data, real-time quotes
2. **Finnhub** - Earnings calendars, financial news
3. **Yahoo Finance** - Stock quotes, options chains (fallback)
4. **Alpha Vantage** - Alternative data source

### Data Hierarchy
```
Live API Data (when available)
    ↓
Enhanced Mock Data (S&P 500 based)
    ↓
Basic Mock Data (fallback)
```

## Features Implemented

### 🔄 **Live Options Chains**
- **Real-time bid/ask spreads** from Polygon.io or Yahoo Finance
- **Live implied volatility** calculations
- **Current volume and open interest** data
- **Greeks calculations** (delta, gamma, theta, vega)
- **Multiple expiration dates** support

### 📊 **Live Expected Move**
- **Real straddle pricing** from live options data
- **Actual implied volatility** calculations
- **Live IV rank and percentile** (when historical data available)
- **Daily/weekly/monthly** move projections
- **Confidence scoring** based on data quality

### 📈 **Live Earnings Data**
- **Real earnings dates** and timing (BMO/AMC)
- **Historical earnings** with actual vs. estimated EPS
- **Beat rates and surprise** calculations
- **Price move analysis** around earnings events
- **Multi-source data** aggregation (Finnhub + Yahoo)

## API Integration Architecture

### Live Data Service (`/lib/services/liveDataService.ts`)
```typescript
// Singleton service managing all live data sources
export const liveDataService = LiveDataService.getInstance();

// Main functions
await fetchLiveOptionsChain(symbol, expiration?)
await fetchLiveEarnings(symbol)
await fetchLiveExpectedMove(symbol)
```

### API Route Updates
All three main APIs now check for live data first:

1. **`/api/options`** - Live options chains with fallback
2. **`/api/expected-move`** - Live IV and straddle calculations
3. **`/api/earnings`** - Live earnings calendar and history

### Caching Strategy
- **Live data**: 5-minute cache (fast refresh)
- **Enhanced mock**: 30-minute cache
- **Basic mock**: 1-hour cache

## Setup Instructions

### 1. Get API Keys (Free Tiers Available)

**Polygon.io** (Recommended for options)
```bash
# Visit: https://polygon.io/
# Free: 5 calls/minute
POLYGON_API_KEY=your_key_here
```

**Finnhub** (Recommended for earnings)
```bash
# Visit: https://finnhub.io/
# Free: 60 calls/minute
FINNHUB_API_KEY=your_key_here
```

**Alpha Vantage** (Optional)
```bash
# Visit: https://www.alphavantage.co/
# Free: 5 calls/minute, 500/day
ALPHA_VANTAGE_API_KEY=your_key_here
```

### 2. Configure Environment
```bash
# Copy example file
cp .env.example .env.local

# Add your API keys to .env.local
```

### 3. Test Live Data
```bash
# Start development server
npm run dev

# Visit any ticker page, e.g.:
# http://localhost:3000/AAPL
# http://localhost:3000/MSFT
# http://localhost:3000/TSLA

# Check console for live data logs:
# [options-api] Live data found for AAPL
# [expected-move-api] Live data found for AAPL
# [earnings-api] Live data found for AAPL
```

## Data Quality Indicators

### Live Data Available
- ✅ **Real-time pricing** and volume
- ✅ **Actual implied volatility**
- ✅ **Live earnings dates**
- ✅ **Current market data**

### Enhanced Mock (No API Keys)
- ⚡ **S&P 500 company data**
- ⚡ **Sector-appropriate pricing**
- ⚡ **Realistic volume/OI**
- ⚡ **Yahoo Finance quotes** (when available)

### Basic Mock (Fallback)
- 🔄 **Static calculations**
- 🔄 **Randomized data**
- 🔄 **Consistent structure**

## Performance Optimizations

### Parallel Data Fetching
```typescript
// Multiple APIs called simultaneously
const [finnhubData, yahooData] = await Promise.allSettled([
  this.fetchFinnhubEarnings(symbol),
  this.fetchYahooEarnings(symbol)
]);
```

### Smart Caching
- **L1 Cache**: In-memory LRU (fastest)
- **L2 Cache**: Redis (shared across instances)
- **TTL Strategy**: Shorter for live data, longer for static

### Error Handling
- **Graceful degradation** to mock data
- **Detailed logging** for debugging
- **No user-facing errors** from API failures

## Monitoring & Debugging

### Console Logs
```bash
# Live data success
[options-api] Live data found for AAPL
[expected-move-api] Live data found for AAPL

# Live data fallback
[options-api] Live data not found for AAPL
[earnings-api] Live data fetch failed for AAPL: API_LIMIT_EXCEEDED
```

### API Response Headers
```json
{
  "x-cache-hit": "miss|l1|l2",
  "x-processing-time": "1234ms",
  "x-data-source": "live|enhanced|mock"
}
```

## Production Deployment

### Environment Variables
```bash
# Required for live data
POLYGON_API_KEY=prod_key
FINNHUB_API_KEY=prod_key

# Redis for caching
UPSTASH_REDIS_REST_URL=prod_url
UPSTASH_REDIS_REST_TOKEN=prod_token
```

### Rate Limiting
- **Polygon**: 5 calls/min (free) → 1000+ calls/min (paid)
- **Finnhub**: 60 calls/min (free) → 600+ calls/min (paid)
- **Yahoo Finance**: No official limits (use responsibly)

### Scaling Considerations
- **Cache hit rates** should be >90% for optimal performance
- **API costs** scale with traffic (monitor usage)
- **Fallback systems** ensure 100% uptime

## Next Steps

1. **Get API keys** for live data sources
2. **Test with real symbols** (AAPL, MSFT, TSLA)
3. **Monitor performance** and cache hit rates
4. **Scale up API plans** as traffic grows
5. **Add more data sources** (IEX Cloud, Quandl, etc.)

---

**Your Quantiv platform now supports professional-grade live financial data!** 🚀
